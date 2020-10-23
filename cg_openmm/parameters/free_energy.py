import os
import numpy as np
import simtk.unit as unit
import matplotlib.pyplot as plt
from cg_openmm.utilities.random_builder import *
from cg_openmm.utilities.iotools import write_pdbfile_without_topology
from openmmtools.multistate import MultiStateReporter, ReplicaExchangeAnalyzer
import pymbar
from scipy import interpolate
from sklearn.utils import resample

kB = unit.MOLAR_GAS_CONSTANT_R # Boltzmann constant
kB = kB.in_units_of(unit.kilojoule/unit.kelvin/unit.mole)

def expectations_free_energy(array_folded_states, temperature_list, frame_begin=0, sample_spacing=1, output_data="output/output.nc",
    bootstrap_energies=None, num_intermediate_states=0):
    """
    This function calculates the free energy difference (with uncertainty) between all conformational states as a function of temperature.

    :param array_folded_states: An array specifying the configurational state of each structure (ranging from 1 to n)
    :type array_folded_states: np.array( int * n_frames*len(temperature_list) ) 

    :param temperature_list: List of temperatures for the simulation data.
    :type temperature_list: List( float * simtk.unit.temperature )
    
    :param frame_begin: index of first frame defining the range of samples to use as a production period (default=0)
    :type frame_begin: int    
    
    :param sample_spacing: spacing of uncorrelated data points, for example determined from pymbar timeseries subsampleCorrelatedData
    :type sample_spacing: int     
    
    :param output_data: Path to the simulation .nc file.
    :type output_data: str
    
    :param num_intermediate_states: Number of unsampled thermodynamic states between sampled states to include in the calculation
    :type num_intermediate_states: int
    
    :param bootstrap_energies: a custom replica_energies array to be used for bootstrapping calculations. Used instead of the energies in the .nc file.
    :type bootstrap_energies: 2d numpy array (float)
    
    :returns:
      - full_T_list - A 1D numpy array listing of all temperatures, including sampled and intermediate unsampled
      - deltaF_values - A dictionary of the form {"statei_statej": 1D numpy array}, containing free energy change for each T in
                        full_T_list, for each conformational state transition.
      - deltaF uncertainty - A dictionary containing 1D numpy arrays of uncertainties corresponding to deltaF_values

    """


    # Number of configurational states:
    n_conf_states = len(np.unique(array_folded_states))

    
    if bootstrap_energies is not None:
        # Use a subsampled replica_energy matrix instead of reading from file
        replica_energies = bootstrap_energies
        
    else:    
        # extract reduced energies and the state indices from the .nc
        reporter = MultiStateReporter(output_data, open_mode="r")
        analyzer = ReplicaExchangeAnalyzer(reporter)
        (
            replica_energies_all,
            unsampled_state_energies,
            neighborhoods,
            replica_state_indices,
        ) = analyzer.read_energies()
        
        # Select production frames to analyze
        replica_energies = replica_energies_all[:,:,frame_begin::sample_spacing]
        
        # Check if array_folded_states needs slicing for production region:
        # array_folded_states is array of [nframes,nreplicas]
        if np.shape(replica_energies)[2] != np.shape(array_folded_states)[0]:
            # Mismatch in the number of frames.
            if np.shape(replica_energies_all[:,:,frame_begin::sample_spacing])[2] == np.shape(array_folded_states[::sample_spacing,:])[0]:
                # Correct starting frame, need to apply sampling stride:
                array_folded_states = array_folded_states[::sample_spacing,:] 
            elif np.shape(replica_energies_all)[2] == np.shape(array_folded_states)[0]:
                # This is the full array_folded_states, slice production frames:
                array_folded_states = array_folded_states[frame_begin::sample_spacing,:]      
                

    # convert the energies from replica/evaluated state/sample form to evaluated state/sample form
    replica_energies = pymbar.utils.kln_to_kn(replica_energies)  
    n_samples = len(replica_energies[0,:])
        
    # Reshape array_folded_states to row vector for pymbar
    # We need to order the data by replica, rather than by frame
    array_folded_states = np.reshape(array_folded_states,(np.size(array_folded_states)),order='F')
        
    # determine the numerical values of beta at each state in units consisten with the temperature
    Tunit = temperature_list[0].unit
    temps = np.array([temp.value_in_unit(Tunit)  for temp in temperature_list])  # should this just be array to begin with
    beta_k = 1 / (kB.value_in_unit(unit.kilojoule_per_mole/Tunit) * temps)

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

    # Calculate N expectations that a structure is in configurational state n
    # We need the probabilities of being in each state - first construct vectors of 0
    # (not in current state) and 1 (in current state)

    bool_i = np.zeros((n_conf_states,array_folded_states.shape[0]))

    for i in range(n_conf_states):
        i_vector = np.full_like(array_folded_states,i)
        # Convert True/False to integer 1/0 for each energy data point:
        bool_i[i] = np.multiply((i_vector==array_folded_states),1)

    # Calculate the expectation of F at each unsampled states

    # Loop over each thermodynamic state:
    results = {}
    for i in range(len(full_T_list)):
        U_n = unsampled_state_energies[i,:]

        # compute expectations of being in conformational state n
        # Store results in a dictionary
        results[str(i)] = mbarT.computeMultipleExpectations(
            bool_i,U_n,compute_covariance=True)

    deltaF_values = {}
    deltaF_uncertainty = {}
    n_trans = 0 # store the number of unique transitions
    F_unit = (-kB*full_T_list[0]*Tunit).unit # units of free energy

    # Initialize the results dictionaries
    for s1 in range(n_conf_states):
        for s2 in range(s1+1,n_conf_states):
            n_trans += 1
            deltaF_values[f"state{s1}_state{s2}"] = np.zeros(len(full_T_list))
            deltaF_uncertainty[f"state{s1}_state{s2}"] = np.zeros(len(full_T_list))
                
    # Compute free energies from probability ratios:
    for i in range(len(full_T_list)):
        for s1 in range(n_conf_states):
            for s2 in range(s1+1,n_conf_states):
                # Free energy change for s2 --> s1 at temp i
                deltaF_values[f"state{s1}_state{s2}"][i] = (
                    -kB*full_T_list[i]*Tunit*(
                    np.log(results[str(i)][0][s1])-
                    np.log(results[str(i)][0][s2]))).value_in_unit(F_unit)
                    
                # Get covariance matrix:
                theta_i = results[str(i)][2]
                deltaF_uncertainty[f"state{s1}_state{s2}"][i] = (
                    kB*full_T_list[i]*unit.kelvin*np.sqrt(
                    theta_i[s1,s1] + theta_i[s2,s2] - (theta_i[s2,s1]+theta_i[s1,s2]))).value_in_unit(F_unit)

    # Add the units back on:
    for s1 in range(n_conf_states):
        for s2 in range(s1+1,n_conf_states):    
            deltaF_values[f"state{s1}_state{s2}"] *= F_unit
            deltaF_uncertainty[f"state{s1}_state{s2}"] *= F_unit
    full_T_list *= Tunit
                    
    return full_T_list, deltaF_values, deltaF_uncertainty
    

def bootstrap_free_energy_folding(array_folded_states, temperature_list, output_data="output/output.nc", frame_begin=0, sample_spacing=1,
    n_sample_boot=500, n_trial_boot=100, num_intermediate_states=0):
    """
    Function for computing uncertainty of free energy, entropy, and enthalpy using standard bootstrapping
    """
    
    # extract reduced energies and the state indices from the .nc
    reporter = MultiStateReporter(output_data, open_mode="r")
    analyzer = ReplicaExchangeAnalyzer(reporter)
    (
        replica_energies_all,
        unsampled_state_energies,
        neighborhoods,
        replica_state_indices,
    ) = analyzer.read_energies()    
    
    # Select production frames to analyze
    replica_energies = replica_energies_all[:,:,frame_begin::sample_spacing]

    # Check if array_folded_states needs slicing for production region:
    # array_folded_states is array of [nframes,nreplicas]
    if np.shape(replica_energies)[2] != np.shape(array_folded_states)[0]:
        # Mismatch in the number of frames.
        if np.shape(replica_energies_all[:,:,frame_begin::sample_spacing])[2] == np.shape(array_folded_states[::sample_spacing,:])[0]:
            # Correct starting frame, need to apply sampling stride:
            array_folded_states = array_folded_states[::sample_spacing,:] 
        elif np.shape(replica_energies_all)[2] == np.shape(array_folded_states)[0]:
            # This is the full array_folded_states, slice production frames:
            array_folded_states = array_folded_states[frame_begin::sample_spacing,:]   
    
    # Overall results:
    deltaF_values = {}
    deltaF_uncertainty = {}
    
    # Uncertainty for each sampling trial:
    deltaF_values_boot = {}
    deltaF_uncertainty_boot = {}
    
    # Units of free energy:
    F_unit = (-kB*unit.kelvin).unit # units of free energy
    
    # Get all possible sample indices
    sample_indices_all = np.arange(0,len(replica_energies[0,0,:]))
    
    for i_boot in range(n_trial_boot):
        sample_indices = resample(sample_indices_all, replace=True, n_samples=n_sample_boot)
        
        n_state = len(array_folded_states[0,:])
        
        # array_folded_states is [n_frame x n_state]
        array_folded_states_resample = np.zeros((n_sample_boot,n_state))
        replica_energies_resample = np.zeros((n_state,n_state,n_sample_boot))
        # replica_energies is [n_states x n_states x n_frame]
        
        # Select the sampled frames from array_folded_states and replica_energies:
        j = 0
        for i in sample_indices:
            array_folded_states_resample[j,:] = array_folded_states[i,:]
            replica_energies_resample[:,:,j] = replica_energies[:,:,i]
            j += 1
            
        # Run free energy expectation calculation:
        full_T_list, deltaF_values_boot[i_boot], deltaF_uncertainty_boot[i_boot] = expectations_free_energy(
            array_folded_states_resample,
            temperature_list,
            bootstrap_energies=replica_energies_resample,
            frame_begin=frame_begin,
            sample_spacing=sample_spacing,
            num_intermediate_states=num_intermediate_states,
        )
        
    arr_deltaF_values_boot = {}    
        
    for key, value in deltaF_values_boot[0].items():
        arr_deltaF_values_boot[key] = np.zeros((n_trial_boot, len(full_T_list)))
        
    # Compute uncertainty over the n_trial_boot trials performed:
    for i_boot in range(n_trial_boot):
        for key, value in deltaF_values_boot[i_boot].items():
            arr_deltaF_values_boot[key][0,:] = value.value_in_unit(F_unit)
            
    for key, value in deltaF_values_boot[0].items():        
        deltaF_uncertainty[key] = np.std(arr_deltaF_values_boot[key],axis=0)*F_unit
        deltaF_values[key] = np.mean(arr_deltaF_values_boot[key],axis=0)*F_unit
        
    return full_T_list, deltaF_values, deltaF_uncertainty
    
    
def get_entropy_enthalpy(deltaF, temperature_list, plotfile_entropy='entropy.pdf', plotfile_enthalpy='enthalpy.pdf'):
    """
    Compute enthalpy change and entropy change upon folding, given free energy of folding for a series of temperatures.
    
    :param deltaF: Free energy of folding for a set of temperatures
    :type deltaF: 1D numpy array
    
    :param deltaF: Uncertainty associated with deltaF
    :type deltaF: 1D numpy array
    
    :param temperature_list: List of temperatures for the simulation data.
    :type temperature_list: List( float * simtk.unit.temperature )
    
    :param plotfile_entropy: path to filename for entropy plot (no plot created if None)
    :type plotfile_entropy: str
    
    :param plotfile_enthalpy: path to filename for enthalpy plot (no plot created if None)
    :type plotfile_enthalpy: str
    
    :returns:
      - deltaS - A 1D numpy array of entropy of folding values for each temperature in temperature_list
      - deltaU - A 1D numpy array of enthalpy of folding values for each temperature in temperature_list
      
    """
    ddeltaF, d2deltaF, spline_tck = get_free_energy_derivative(deltaF, temperature_list)
    
    F_unit = deltaF[0].unit
    T_unit = temperature_list[0].unit
    S_unit = F_unit/T_unit
    U_unit = F_unit
    
    # Spline fitting function strips off units - add back:
    deltaS = -ddeltaF * F_unit / T_unit
    
    deltaU = deltaF + temperature_list*deltaS
    
    if plotfile_entropy is not None:
        figure = plt.figure()
        plt.plot(
            temperature_list.value_in_unit(T_unit),
            deltaS.value_in_unit(S_unit),
            'o-',
            linewidth=1,
            markersize=6,
            fillstyle='none',
        )
        
        xlabel = f'Temperature {T_unit.get_symbol()}'
        ylabel = f'Entropy of folding {S_unit.get_symbol()}' 
        
        plt.xlabel(xlabel)
        plt.ylabel(ylabel)
        plt.savefig(f"{plotfile_entropy}")

    if plotfile_enthalpy is not None:
        figure = plt.figure()
        plt.plot(
            temperature_list.value_in_unit(T_unit),
            deltaU.value_in_unit(U_unit),
            'o-',
            linewidth=1,
            markersize=6,
            fillstyle='none',
        )
        
        xlabel = f'Temperature {T_unit.get_symbol()}'
        ylabel = f'Enthalpy of folding {U_unit.get_symbol()}' 
        
        plt.xlabel(xlabel)
        plt.ylabel(ylabel)
        plt.savefig(f"{plotfile_enthalpy}")    
        
    return deltaS, deltaU
  
        
    
def get_free_energy_derivative(deltaF, temperature_list, plotfile=None):
    """
    Fit a heat capacity vs T dataset to cubic spline, and compute derivatives
    
    :param deltaF: free energy of folding data series
    :type deltaF: Quantity or numpy 1D array
    
    :param temperature_list: List of temperatures used in replica exchange simulations
    :type temperature: Quantity or numpy 1D array
    
    :param plotfile: path to filename to output plot (default=None)
    :type plotfile: str
    
    :returns:
          - dF_out ( 1D numpy array (float) ) - 1st derivative of free energy, from a cubic spline evaluated at each point in deltaF)
          - d2F_out ( 1D numpy array (float) ) - 2nd derivative of free energy, from a cubic spline evaluated at each point in deltaF)
          - spline_tck ( scipy spline object (tuple) ) - knot points (t), coefficients (c), and order of the spline (k) fit to deltaF data
    
    """
    xdata = temperature_list
    ydata = deltaF
    
    # Strip units off quantities:
    if type(xdata[0]) == unit.quantity.Quantity:
        xdata_val = np.zeros((len(xdata)))
        xunit = xdata[0].unit
        for i in range(len(xdata)):
            xdata_val[i] = xdata[i].value_in_unit(xunit)
        xdata = xdata_val
    
    if type(ydata[0]) == unit.quantity.Quantity:
        ydata_val = np.zeros((len(ydata)))
        yunit = ydata[0].unit
        for i in range(len(ydata)):
            ydata_val[i] = ydata[i].value_in_unit(yunit)
        ydata = ydata_val
            
    # Fit cubic spline to data, no smoothing
    spline_tck = interpolate.splrep(xdata, ydata, s=0)
    
    xfine = np.linspace(xdata[0],xdata[-1],1000)
    yfine = interpolate.splev(xfine, spline_tck, der=0)
    dF = interpolate.splev(xfine, spline_tck, der=1)
    d2F = interpolate.splev(xfine, spline_tck, der=2)
    
    dF_out = interpolate.splev(xdata, spline_tck, der=1)
    d2F_out = interpolate.splev(xdata, spline_tck, der=2)
    
    if plotfile != None:
        figure, axs = plt.subplots(nrows=3,ncols=1,sharex=True)
        
        axs[0].plot(xdata,ydata,'ok',
            markersize=4,
            fillstyle='none',
            label='simulation data',
        )
        
        axs[0].plot(xfine,yfine,'-b',
            label='cubic spline',
        )
        
        axs[0].legend()
        axs[0].set_ylabel(r'$\Delta F (J/mol)$')
        
        axs[1].plot(xfine,dF,'-r',
            label=r'$\frac{d\Delta F}{dT}$',
        )
        
        axs[1].legend()
        axs[1].set_ylabel(r'$\frac{d\Delta F}{dT}$')
        
        axs[2].plot(xfine,d2F,'-g',
            label=r'$\frac{d^{2}\Delta F}{dT^{2}}$',
        )
        
        axs[2].legend()
        axs[2].set_ylabel(r'$\frac{d^{2}\Delta F}{dT^{2}}$')
        axs[2].set_xlabel(r'$T (K)$')
        
        plt.tight_layout()
        
        plt.savefig(plotfile)
        plt.close()
    
    return dF_out, d2F_out, spline_tck    
    

def plot_free_energy_results(full_T_list, deltaF_values, deltaF_uncertainty,plotfile="free_energy_plot.pdf"):   
    """
    Plot free energy difference data for each conformational state transition as a function of temperature.

    :param full_T_list: Array listing of all temperatures, including sampled and intermediate unsampled
    :type full_T_list: 1D numpy array
    
    :param deltaF_values: A dictionary containing free energy change for each T in full_T_list, for each conformational state transition.
    :type deltaF_values: dict{"statei_statej":1D numpy array}
    
    :param deltaF_uncertainty: A dictionary containing uncertainties corresponding to deltaF_values
    :type deltaF_uncertainty: dict{"statei_statej":1D numpy array}
    
    :param plotfile: name of file, including pdf extension
    :type plotfile: str
    
    """

    T_unit = full_T_list[0].unit
    F_unit = list(deltaF_values.items())[0][1].unit
    
    xlabel = f'Temperature {T_unit.get_symbol()}'
    ylabel = f'Free energy change {F_unit.get_symbol()}'
    legend_str = []

    for key,value in deltaF_values.items():
        plt.errorbar(
            full_T_list.value_in_unit(T_unit),
            deltaF_values[f"{key}"].value_in_unit(F_unit),
            deltaF_uncertainty[f"{key}"].value_in_unit(F_unit),
            linewidth=1,
            markersize=6,
            fmt='o-',
            fillstyle='none',
            capsize=4,
        )
        legend_str.append(key)
        
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    pyplot.legend(legend_str)
    plt.savefig(f"{plotfile}")

    return