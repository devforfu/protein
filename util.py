import pickle
from typing import List


def load_pickle(filename):
    with open(filename, 'rb') as f:
        return pickle.load(f)


