import msprime
import numpy as np
import tskit
import pandas as pd
from numba import jit
import tstrait.genotype as genotype

"""
Phenotypic simulation model from the infinite sites model
"""

def obtain_sample_index_map(num_nodes, sample_id_list):
    sample_index_map = np.zeros(num_nodes + 1, dtype=int) - 1
    for j, u in enumerate(sample_id_list):
        sample_index_map[u] = j
    return sample_index_map

def choose_causal(ts, num_causal, random_seed = None):
    """
    Choose causal sites from tree sequence data and return the site ID
    """
    rng = np.random.default_rng(random_seed)
    if type(ts) != tskit.trees.TreeSequence:
        raise TypeError("Input should be a tree sequence data")  
    if not isinstance(num_causal, int):
        raise TypeError("Number of causal sites should be a non-negative integer")
    if num_causal < 0:
        raise ValueError("Number of causal sites should be a non-negative integer")
    
    num_sites = ts.num_sites
    
    if num_sites == 0:
        raise ValueError("No mutation in the provided data")
    if num_causal > num_sites:
        raise ValueError("There are more causal sites than the number of mutations inside the tree sequence")

    site_id = rng.choice(range(num_sites), size=num_causal, replace=False)
    
    return site_id
    
def sim_effect_size(num_causal, trait_mean, trait_sd, random_seed = None):
    """
    Simulate effect sizes from a normal distribution
    """
    rng = np.random.default_rng(random_seed)
    if not isinstance(num_causal, int):
        raise TypeError("Number of causal sites should be a non-negative integer")
    if num_causal < 0:
        raise ValueError("Number of causal sites should be a non-negative integer")
    if (not isinstance(trait_sd, int) and not isinstance(trait_sd, float)):
        raise TypeError("Standard deviation should be a non-negative number")
    if trait_sd <= 0:
        raise ValueError("Standard deviation should be a non-negative number")
    if (not isinstance(trait_mean, int) and not isinstance(trait_mean, float)):
        raise TypeError("Trait mean should be a float or integer data type") 
    
    beta = rng.normal(loc=trait_mean, scale=trait_sd/ np.sqrt(num_causal), size=num_causal)
    
    return beta

def environment(G, h2, trait_sd, random_seed = None):
    """
    Add environmental noise
    """
    rng = np.random.default_rng(random_seed)
    if len(G) == 0:
        raise ValueError("No individuals in the simulation model")
    if (not isinstance(h2, int) and not isinstance(h2, float)) or h2 > 1 or h2 < 0:
        raise ValueError("Heritability should be 0 <= h2 <= 1")
    num_ind = len(G)
    if h2 == 1:
        E = np.zeros(num_ind)
        phenotype = G
    elif h2 == 0:
        E = rng.normal(loc=0.0, scale=trait_sd, size=num_ind)
        phenotype = E
    else:
        E = rng.normal(loc=0.0, scale=np.sqrt((1-h2)/h2 * np.var(G)), size=num_ind)
        phenotype = G + E
    return phenotype, E

def causal_allele(ts, site_id, random_seed = None):
    """
    Obtain causal alleles from causal sites, and return the ancestral state, causal allele, and genomic location
    They are aligned based on their genomic positions
    """
    rng = np.random.default_rng(random_seed)
    if type(ts) != tskit.trees.TreeSequence:
        raise TypeError("Input should be a tree sequence data")  
    location = np.zeros(len(site_id))
    ancestral = np.zeros(len(site_id))
    causal_allele = np.zeros(len(site_id), dtype=int)
    
    for i, single_id in enumerate(site_id_list):
        allele_list = np.array([])
        for m in ts.site(single_id).mutations:
            if m.derived_state != ts.site(single_id).ancestral_state:
                allele_list = np.append(allele_list, m.derived_state)

        causal_allele[i] = rng.choice(np.unique(allele_list))
        location[i] = ts.site(single_id).position
        ancestral[i] = ts.site(single_id).ancestral_state
    
    coordinate = np.argsort(location)
    location = location[coordinate]
    site_id = site_id[coordinate]
    ancestral = ancestral[coordinate]
    causal_allele = causal_allele[coordinate]
    
    return site_id, ancestral, causal_allele, location

@jit(nopython=True)
def update_node_values_array_access(root, left_child_array, right_sib_array, node_values, G):
    """
    Tree traversal algorithm
    """
    stack = [root]
    while stack:
        parent = stack.pop()
        child = left_child_array[parent]
        if child != -1:
            while child != -1:
                node_values[child] += node_values[parent]
                stack.append(child)
                child = right_sib_array[child]
        else:
            G[parent] += node_values[parent]


def obtain_sample_nodes(root, left_child_array, right_sib_array):
    """
    Obtain sample nodes that are below the root
    """
    stack = [root]
    sample_node = []
    while stack:
        parent = stack.pop()
        child = left_child_array[parent]
        if child != -1:
            while child != -1:
                stack.append(child)
                child = right_sib_array[child]
        else:
            sample_node.append(child)
    return sample_node

def remove_list(sample_nodes, remove_nodes):
    """
    Remove elements in remove_nodes from sample_nodes
    """
    for i in remove_nodes:
        sample_nodes.remove(i)
    return sample_nodes

def causal_mutation_sample_nodes(ts, tree, site, causal_allele):
    """
    Obtain list of sample nodes that have the causal mutation
    """
    sample_nodes = []
    mutation_list = sorted(site.mutations, key=lambda x:x.time, reverse=True)
    
    for m in mutation_list:
        if m.derived_state == causal_allele:
            keep_nodes = obtain_sample_nodes(m.node, tree.left_child_array, tree.right_sib_array)
            sample_nodes += keep_nodes
        elif ts.mutation(m.parent).derived_state == causal_allele:
            remove_nodes = obtain_sample_nodes(m.node, tree.left_child_array, tree.right_sib_array)
            sample_nodes = remove_list(sample_nodes, remove_nodes)
    
    return sample_nodes

def node_genetic_value(ts, site_id, location, causal_allele, trait_mean, trait_sd, rng, model):
    """
    Obtain genetic values of nodes and effect sizes of causal sites
    The mutation inside the algorithm are aligned based on their genetic position
    """
    if type(ts) != tskit.trees.TreeSequence:
        raise TypeError("Input should be a tree sequence data")  
    size_G = np.max(ts.samples())+1
    G = np.zeros(size_G)
    beta_list = np.zeros(len(location))
    frequency = np.zeros(len(location))
    
    tree = ts.first()
    N = ts.num_samples
    num_causal = len(location)
    
    for i, loc in enumerate(location):
        tree.seek(loc)
        causal_sample_nodes = causal_mutation_sample_nodes(ts, tree, ts.site(site_id[i]), causal_allele[i])
        freq_allele = len(causal_sample_nodes) / N
        frequency[i] = freq_allele
        # Ideally I would like to have a model here
        # No function written effectively
        beta = sim_beta(...)
        beta_list[i] = beta
        G[causal_sample_nodes] += beta

    return G, beta_list, frequency
    

def individual_genetic(ts, G):
    """
    Convert genetic value of nodes to be genetic value of individuals
    """
    G = G[ts.samples()]
    G = G[::2] + G[1::2]
    return G


def phenotype_sim(ts, num_causal, trait_mean=0, trait_sd=1, h2=0.3, model = None, random_seed=None):
    if type(ts) != tskit.trees.TreeSequence:
        raise TypeError("Input should be a tree sequence data")    
    site_id = choose_causal(ts, num_causal, random_seed)
    
    site_id, ancestral, causal_allele, location = causal_allele(ts, site_id, random_seed)
    
    G, beta, frequency = node_genetic_value(ts, site_id, location, causal_allele, trait_mean, trait_sd, model, random_seed)
    
    G = individual_genetic(ts, G)
    
    phenotype, E = environment(G, h2, trait_sd, random_seed)
    assert len(phenotype) == ts.num_individuals
    
    
    # Phenotype dataframe
#    pheno_df = pd.DataFrame({"Individual ID": [s.id for s in ts.individuals()],
#                             "Phenotype": phenotype,"Environment":E,"Genotype": G})
    pheno_df = pd.DataFrame({"Individual ID": list(range(ts.num_individuals)),
                              "Phenotype": phenotype,"Environment":E,"Genotype": G})
    
    # Genotype dataframe
    gene_df = pd.DataFrame({
        "Site ID": site_id,
        "Ancestral State": ancestral,
        "Causal State": causal_allele,
        "Location": location,
        "Effect Size": beta,
        "Frequency": frequency
    })

    return pheno_df, gene_df