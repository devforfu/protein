import json
import sys
import pickle

from tqdm import tqdm


def load_pickle(filename):
    with open(filename, 'rb') as f:
        return pickle.load(f)


def train_test(items):
    train, test = [], []
    for item in tqdm(items):
        subset = item.pop('dataset')
        (train if subset == 'train' else test).append(item)
    return train, test


def take_site(items, site):
    return [item for item in items if item['site'] == site]


def get_num_classes(items):
    return np.unique([item['sirna'] for item in items]).shape[0]



if __name__ == '__main__':
    filename = sys.argv[1]
    items = load_pickle(filename)
    train, test = train_test(items)
    with open('train.json', 'w') as f:
        json.dump(train, f)
    with open('test.json', 'w') as f:
        json.dump(test, f)

