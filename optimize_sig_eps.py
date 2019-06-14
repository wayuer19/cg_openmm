#!/usr/bin/python

import numpy as np
import matplotlib.pyplot as pyplot
# OpenMM utilities
import mdtraj as md
from simtk import unit
# foldamers utilities
from foldamers.src.cg_model.cgmodel import basic_cgmodel
# cg_openmm utilities
from cg_openmm.src.simulation.rep_exch import replica_exchange

# Simulation settings
simulation_time_step = 0.01 * unit.femtosecond

# Define static model settings
backbone_length = 1 # Number of backbone beads
sidechain_length = 1 # Number of sidechain beads
sidechain_positions = [0] # Index of backbone bead(s) on which the side chains are placed
polymer_length = 8 # Number of monomers in the polymer
mass = 10.0 * unit.amu # Mass of beads
bond_length = 1.0 * unit.angstrom # bond length

# Set variable model settings
base_sigma = 2.4 * unit.angstrom # Lennard-Jones interaction distance
base_epsilon = 0.5 * unit.kilocalorie_per_mole # Lennard-Jones interaction strength
sigma_list = [(base_sigma).__add__(i * base_sigma.unit) for i in [ j * 0.2 for j in range(-2,3,1)]]
epsilon_list = [(base_epsilon).__add__(i * base_epsilon.unit) for i in [ j * 0.2 for j in range(-1,3,1)]]
sigma_epsilon_list = np.zeros((len(sigma_list),len(epsilon_list)))

for sigma_index in range(len(sigma_list)):
  for epsilon_index in range(len(epsilon_list)):
    sigma = sigma_list[sigma_index]
    epsilon = epsilon_list[epsilon_index]
    print("Evaluating the energy for a model with:")
    print("sigma="+str(sigma)+" and epsilon="+str(epsilon))

    # Build a coarse grained model
    cgmodel = basic_cgmodel(polymer_length=polymer_length, backbone_length=backbone_length, sidechain_length=sidechain_length, sidechain_positions=sidechain_positions, mass=mass, sigma=sigma, epsilon=epsilon, bond_length=bond_length)
    
    # Run a replica exchange simulation for this model:
    replica_energies,temperatures = replica_exchange(cgmodel.topology,cgmodel.system,cgmodel.positions,simulation_time_step=simulation_time_step)
    print(replica_energies)

exit()
