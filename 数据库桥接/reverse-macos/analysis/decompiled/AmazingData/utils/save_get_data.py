# Source Generated with Decompyle++
# File: save_get_data.pyc (Python 3.12)

import os
import pandas as pd
import warnings
import pickle
warnings.filterwarnings('ignore')

def save_data_to_hdf5(path, data_name, input_data, is_append = (False,)):
    if not os.path.exists(path):
        os.makedirs(path)
    input_data.to_hdf(path + data_name + '.h5', key = data_name, mode = 'w', append = is_append)


def get_data_from_hdf5(path, data_name):
    return pd.read_hdf(path + data_name + '.h5')


def save_data_to_pkl(path, data_name, input_data = (None,)):
    pass
# WARNING: Decompyle incomplete


def get_data_from_pkl(path, data_name):
    f = open(path + data_name + '.pkl', 'rb')
    loaded_dict = pickle.load(f)
    None(None, None)
    return loaded_dict
    with None:
        if not None:
            pass
# WARNING: Decompyle incomplete

