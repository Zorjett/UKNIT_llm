"""
This file contains some of the generic utility functions 
"""
import pickle
import os

def pickle_load(file):
    with open(file, "rb") as f:
        member = pickle.load(f)
    return member


def pickle_dump(file,member):
    with open(file, "wb") as f:
        pickle.dump(member, f)

def write_to_file(file,statements):
    os.makedirs(os.path.dirname(os.path.abspath(file)), exist_ok=True)
    if not statements:
        open(file, 'w').close()
        return
    with open(file,'w') as f:
        for i in range(len(statements)-1):
            print(statements[i],file=f)
        print(statements[-1],file=f,end='')
