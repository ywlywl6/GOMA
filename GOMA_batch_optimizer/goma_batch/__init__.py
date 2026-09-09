"""Batch-level resource allocation for the reviewed GOMA models."""
from .core import Problem, Profile
from .adaptive import solve_adaptive
__version__ = '0.1.0'
__all__ = ['Problem', 'Profile', 'solve_adaptive']
