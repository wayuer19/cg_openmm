#!~/anaconda3/bin/python

import os
import pickle

from cg_openmm.thermo.calc import *
from simtk import unit

# This example demonstrates how to calculate heat capacity as a function of temperature from
# replica exchange energies, with uncertainties estimated using pyMBAR.

# Note: process_replica_exchange_data should first be run to determine the determine the start
# of the production region and energy decorrelation time.


# Job settings
output_directory = '../run_replica_exchange/output'
output_data = os.path.join(output_directory, "output.nc")

# Load in trajectory stats:
analysis_stats = pickle.load(open("../run_replica_exchange/analysis_stats_discard_20ns.pkl","rb"))

# Read the simulation coordinates for individual temperature replicas                                                                     
C_v, dC_v, new_temperature_list = get_heat_capacity(
    output_data=output_data,
    frame_begin=analysis_stats["production_start"],
    sample_spacing=analysis_stats["energy_decorrelation"],
    num_intermediate_states=3,
    plot_file="heat_capacity.pdf",
)

print(f"T({new_temperature_list[0].unit})  Cv({C_v[0].unit})  dCv({dC_v[0].unit})")
for i, C in enumerate(C_v):
    print(f"{new_temperature_list[i]._value:>8.2f}{C_v[i]._value:>10.4f} {dC_v[i]._value:>10.4f}")
