import os, subprocess
import numpy as np
import simtk.unit as unit
from statistics import mean
from scipy.stats import linregress
from scipy import spatial
import matplotlib.pyplot as plt
from cg_openmm.utilities.random_builder import *
from cg_openmm.utilities.iotools import write_pdbfile_without_topology
from openmmtools.multistate import MultiStateReporter, ReplicaExchangeAnalyzer
import pymbar
import mdtraj as md

kB = unit.MOLAR_GAS_CONSTANT_R # Boltzmann constant

def get_native_contacts(cgmodel, native_structure, native_contact_native_contact_cutoff_ratio):
    """
        Given a coarse grained model, positions for that model, and positions for the native structure, this function calculates the fraction of native contacts for the model.

        :param cgmodel: CGModel() class object
        :type cgmodel: class

        :param native_structure: Positions for the particles in a coarse grained model.
        :type native_structure: np.array( float * unit.angstrom ( num_particles x 3 ) )

        :param native_contact_native_contact_cutoff_ratio: The maximum distance for two nonbonded particles that are defined as "native",default=None
        :type native_contact_native_contact_cutoff_ratio: `Quantity() <https://docs.openmm.org/development/api-python/generated/simtk.unit.quantity.Quantity.html>`_

        :returns:
          - native_contact_list - A list of the nonbonded interactions whose inter-particle distances are less than the 'native_contact_cutoff_distance'.
          - native_contact_distances - A Quantity numpy array of the native pairwise distances corresponding to native_contact_list
        """

    nonbonded_interaction_list = cgmodel.nonbonded_interaction_list
    native_structure_distances = distances(nonbonded_interaction_list, native_structure)
    native_contact_list = []
    native_contact_distances_list = []
    
    for interaction in range(len(nonbonded_interaction_list)):
        if native_structure_distances[interaction] < (native_contact_native_contact_cutoff_ratio):
            native_contact_list.append(nonbonded_interaction_list[interaction])
            native_contact_distances_list.append(distances(native_contact_list, native_structure))
    
    # Units get messed up if converted using np.asarray
    native_contact_distances = np.zeros((len(native_contact_distances_list)))
    for i in range(len(native_contact_distances_list)):
        native_contact_distances[i] = native_contact_distances_list[i][0].value_in_unit(unit.nanometer)
    native_contact_distances *= unit.nanometer
    
    return native_contact_list, native_contact_distances


def expectations_fraction_contacts(native_contact_list, native_contact_distances, temperature_list, pdb_file_list, native_contact_cutoff_ratio=1.00, frame_begin=0, output_directory="output", output_data="output.nc", num_intermediate_states=0):
    """
    Given a .nc output, a temperature list, and a number of intermediate states to insert for the temperature list, this function calculates the native contacts expectation.
    
    :param native_contact_list: A list of the nonbonded interactions whose inter-particle distances are less than the 'native_contact_cutoff_distance'.
    :type native_contact_list: List
    
    :param native_contact_distances: A numpy array of the native pairwise distances corresponding to native_contact_list
    :type native_contact_distances: Quantity
    
    :param temperature_list: List of temperatures corresponding to the states in the .nc output file
    :type temperature: List( float * simtk.unit.temperature )
    
    :param pdb_file_list: A list of replica PDB files corresponding to the .nc file
    :type pdb_file_list: List( str )    
    
    :param native_contact_cutoff_ratio: The distance below which two nonbonded, interacting particles in a non-native pose are assigned as a "native contact", as a ratio of the distance for that contact in the native structure, default=1.00
    :type native_contact_cutoff_ratio: float
    
    :param frame_begin: index of first frame defining the range of samples to use as a production period (default=0)
    :type frame_begin: int

    :param output_directory: directory in which the output data is in, default = "output"                                     
    :type output_data: str    
    
    :param output_data: Path to the output data for a NetCDF-formatted file containing replica exchange simulation data, default = None                                                                                                  
    :type output_data: str
    
    :param num_intermediate_states: The number of states to insert between existing states in 'temperature_list'
    :type num_intermediate_states: int    
    
    """
    
    max_native_contacts = len(native_contact_list)

    # extract reduced energies and the state indices from the .nc  
    reporter = MultiStateReporter(os.path.join(output_directory,output_data), open_mode="r")
    analyzer = ReplicaExchangeAnalyzer(reporter)
    (
        replica_energies_all,
        unsampled_state_energies,
        neighborhoods,
        replica_state_indices,
    ) = analyzer.read_energies()
    
    # Select production frames to analyze
    replica_energies = replica_energies_all[:,:,frame_begin:]

    # determine the numerical values of beta at each state in units consistent with the temperature
    Tunit = temperature_list[0].unit
    temps = np.array([temp.value_in_unit(Tunit)  for temp in temperature_list])  # should this just be array to begin with
    beta_k = 1 / (kB.value_in_unit(unit.kilojoule_per_mole/Tunit) * temps)

    # convert the energies from replica/evaluated state/sample form to evaluated state/sample form
    replica_energies = pymbar.utils.kln_to_kn(replica_energies)
    n_samples = len(replica_energies[0,:])
    # n_samples in [nreplica x nsamples_per_replica]
    
    # calculate the number of states we need expectations at.  We want it at all of the original
    # temperatures, each intermediate temperature, and then temperatures +/- from the original
    # to take finite derivatives.

    # create  an array for the temperature and energy for each state, including the
    # finite different state.
    n_sampled_T = len(temps)
    n_unsampled_states = (n_sampled_T + (n_sampled_T-1)*num_intermediate_states)
    unsampled_state_energies = np.zeros([n_unsampled_states,n_samples])
    full_T_list = np.zeros(n_unsampled_states)

    # delta is the spacing between temperatures.
    delta = np.zeros(n_sampled_T-1)

    # fill in a list of temperatures at all original temperatures and all intermediate states.
    full_T_list[0] = temps[0]  
    t = 0
    for i in range(n_sampled_T-1):
        delta[i] = (temps[i+1] - temps[i])/(num_intermediate_states+1)
        for j in range(num_intermediate_states+1):
            full_T_list[t] = temps[i] + delta[i]*j
            t += 1
    full_T_list[t] = temps[-1]
    n_T_vals = t+1

    # calculate betas of all of these temperatures
    beta_full_k = 1 / (kB.value_in_unit(unit.kilojoule_per_mole/Tunit) * full_T_list)
    
    ti = 0
    N_k = np.zeros(n_unsampled_states)
    for k in range(n_unsampled_states):
        # Calculate the reduced energies at all temperatures, sampled and unsample.
        unsampled_state_energies[k, :] = replica_energies[0,:]*(beta_full_k[k]/beta_k[0])
        if ti < len(temps):
            # store in N_k which states do and don't have samples.
            if full_T_list[k] == temps[ti]:
                ti += 1
                N_k[k] = n_samples//len(temps)  # these are the states that have samples

    # call MBAR to find weights at all states, sampled and unsampled
    mbarT = pymbar.MBAR(unsampled_state_energies,N_k,verbose=False, relative_tolerance=1e-12);
    
    # Now we have the weights at all temperatures, so we can
    # calculate the expectations.  We now need a number of native contacts for each
    # structure.
    n_samples_per_replica = n_samples//n_sampled_T
    Q = np.zeros((n_samples_per_replica,n_sampled_T))

    # calculate Q (fraction of native contacts) for each structure.
    # because we need to iterate per state, we have to do some bookkeeping.
    # The sampled state energies are stored in order of the replicas, so the
    # samples need to correspond to the same order.

    # Use MDTraj to compute all of the native contact fractions
    for replica_index in range(n_sampled_T):
        rep_traj = md.load(pdb_file_list[replica_index])
        Q[:,replica_index] = fraction_native_contacts(
            rep_traj[frame_begin:],
            native_contact_list,
            native_contact_distances,
            native_contact_cutoff_ratio,
        )
        
    # Reshape column by column for pymbar
    Q = np.reshape(Q,np.size(Q), order='F')
            
    # calculate the expectation of Q at each unsampled states         
    results = mbarT.computeExpectations(Q)  # compute expectations of Q at all points
    Q_expect = results[0]
    dQ_expect = results[1]

    # return the results in a dictionary (better than in a list)
    return_results = dict()
    return_results["T"] = full_T_list
    return_results["Q"] = Q_expect
    return_results["dQ"] = dQ_expect

    return return_results

    
def fraction_native_contacts(
    traj,
    native_contact_list,
    native_contact_distances,
    native_contact_cutoff_ratio=1.00,
):
    """
    Given an mdtraj trajectory object, and positions for the native structure, this function calculates the fraction of native contacts for the model.
    
    :param traj: a loaded mdtraj trajectory
    :type traj: mdtraj trajectory object

    :param native_contact_list: A list of the nonbonded interactions whose inter-particle distances are less than the 'native_contact_cutoff_distance'.
    :type native_contact_list: List
    
    :param native_contact_distances: A numpy array of the native pairwise distances corresponding to native_contact_list
    :type native_contact_distances: Quantity

    :param native_contact_cutoff_ratio: The distance below which two nonbonded, interacting particles in a non-native pose are assigned as a "native contact", as a ratio of the distance for that contact in the native structure, default=1.00
    :type native_contact_cutoff_ratio: float

    :returns:
      - Q ( numpy array (float * nframes) ) - The fraction of native contacts for all frames in the trajectory.

    """

    if len(native_contact_list)==0:
        print("ERROR: there are 0 'native' interactions with the current cutoff distance.")
        print("Try increasing the 'native_structure_contact_native_contact_cutoff_ratio'")
        exit()

    nframes = traj.n_frames    
        
    traj_distances = mdtraj.compute_distances(
        traj,native_contact_list,periodic=False,opt=True)
    # This produces a [nframe x len(native_contacts)] array            
    nc_unit = native_contact_distances.unit        
            
    # Compute Boolean matrix for whether or not a distance is native
    native_contact_matrix = (traj_distances<(native_contact_cutoff_ratio*native_contact_distances.value_in_unit(nc_unit)))

    number_native_interactions=np.sum(native_contact_matrix,axis=1)

    Q = number_native_interactions/len(native_contact_distances)
    return Q


def optimize_Q(cgmodel, native_structure, ensemble):
    """
        Given a coarse grained model and a native structure as input

        :param cgmodel: CGModel() class object
        :type cgmodel: class

        :param native_structure: Positions for the native structure.
        :type native_structure: np.array( float * unit.angstrom ( num_particles x 3 ) )

        :param ensemble: A list of poses that will be used to optimize the cutoff distance for defining native contacts
        :type ensemble: List(positions(np.array(float*simtk.unit (shape = num_beads x 3))))

        :returns:
          - native_structure_contact_native_contact_cutoff_ratio ( `Quantity() <https://docs.openmm.org/development/api-python/generated/simtk.unit.quantity.Quantity.html>`_ ) - The ideal distance below which two nonbonded, interacting particles should be defined as a "native contact"
        """

    cutoff_list = [(0.95 + i * 0.01) * cgmodel.get_sigma(0) for i in range(30)]

    cutoff_Q_list = []
    for cutoff in cutoff_list:
        Q_list = []
        for pose in ensemble:
            Q = fraction_native_contacts(
                cgmodel, pose, native_structure, native_structure_contact_native_contact_cutoff_ratio=cutoff
            )
            Q_list.append(Q)

        mean_Q = mean(Q_list)
        cutoff_Q_list.append(mean_Q)

    cutoff_Q_list.index(max(cutoff_Q_list))

    native_structure_contact_native_contact_cutoff_ratio = cutoff_Q_list.index(max(cutoff_Q_list))

    return native_structure_contact_native_contact_cutoff_ratio
    

def plot_native_contact_fraction(temperature_list, Q, Q_uncertainty,plotfile="Q_vs_T.pdf"):
    """
    Given a list of temperatures and corresponding native contact fractions, plot Q vs T.

    :param temperature_list: List of temperatures that will be used to define different replicas (thermodynamics states), default = None
    :type temperature_list: List( `SIMTK <https://simtk.org/>`_ `Unit() <http://docs.openmm.org/7.1.0/api-python/generated/simtk.unit.unit.Unit.html>`_ * number_replicas )

    :param Q: native contact fraction for a given temperature
    :type Q: np.array(float * len(temperature_list))
    
    :param Q_uncertainty: uncertainty associated with Q
    :type Q_uncertainty: np.array(float * len(temperature_list))
    
    """
    temperature_array = np.zeros((len(temperature_list)))
    for i in range(len(temperature_list)):
        temperature_array[i] = temperature_list[i].value_in_unit(unit.kelvin)
    
    plt.errorbar(
        temperature_array,
        Q,
        Q_uncertainty,
        linewidth=0.5,
        markersize=4,
        fmt='o-',
        fillstyle='none',
        capsize=4,
    )

    plt.xlabel("T (K)")
    plt.ylabel("Native contact fraction")
    plt.savefig(plotfile)
    
    
        