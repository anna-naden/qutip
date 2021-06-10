# This file is part of QuTiP: Quantum Toolbox in Python.
#
#    Copyright (c) 2011 and later, Paul D. Nation and Robert J. Johansson.
#    All rights reserved.
#
#    Redistribution and use in source and binary forms, with or without
#    modification, are permitted provided that the following conditions are
#    met:
#
#    1. Redistributions of source code must retain the above copyright notice,
#       this list of conditions and the following disclaimer.
#
#    2. Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#
#    3. Neither the name of the QuTiP: Quantum Toolbox in Python nor the names
#       of its contributors may be used to endorse or promote products derived
#       from this software without specific prior written permission.
#
#    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#    "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#    LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
#    PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
#    HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
#    SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
#    LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
#    DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
#    THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
#    (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
#    OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
###############################################################################

__all__ = [
    'liouvillian', 'lindblad_dissipator', 'operator_to_vector',
    'vector_to_operator', 'stack_columns', 'unstack_columns', 'stacked_index',
    'unstacked_index', 'spost', 'spre', 'sprepost', 'reshuffle',
]

import functools

import numpy as np

from .qobj import Qobj
from . import data as _data

def _map_over_compound_operators(f):
    """
    Convert a function which takes Qobj into one that can also take compound
    operators like QobjEvo, and applies itself over all the components.
    """
    @functools.wraps(f)
    def out(qobj):
        from .cy.qobjevo import QobjEvo
        if isinstance(qobj, QobjEvo):
            return qobj.linear_map(f, _skip_check=True)
        if not isinstance(qobj, Qobj):
            raise TypeError("expected a quantum object")
        return f(qobj)
    return out


def liouvillian(H=None, c_ops=None, data_only=False, chi=None):
    """Assembles the Liouvillian superoperator from a Hamiltonian
    and a ``list`` of collapse operators.

    Parameters
    ----------
    H : Qobj or QobjEvo (optional)
        System Hamiltonian or Hamiltonian component of a Liouvillian.
        Considered `0` if not given.

    c_ops : array_like of Qobj or QobjEvo
        A ``list`` or ``array`` of collapse operators.

    data_only :  bool [False]
        Return the data object instead of a Qobj

    Returns
    -------
    L : Qobj or QobjEvo
        Liouvillian superoperator.

    """
    from .cy.qobjevo import QobjEvo
    c_ops = c_ops or []
    if isinstance(c_ops, (Qobj, QobjEvo)):
        c_ops = [c_ops]
    if chi and len(chi) != len(c_ops):
        raise ValueError('chi must be a list with same length as c_ops')
    chi = chi or [0] * len(c_ops)

    if H is None:
        # No Hamiltonian, add the lindblad_dissipator of c_ops:
        if not c_ops:
            raise ValueError("The liouvillian need an Hamiltonian"
                             " and/or c_ops")
        out = sum(lindblad_dissipator(c_op, chi=chi_)
                  for c_op, chi_ in zip(c_ops, chi))
        return out.data if isinstance(out, Qobj) and data_only else out
    elif not H.isoper:
        raise TypeError("Invalid type for Hamiltonian.")

    if isinstance(H, QobjEvo) or any(isinstance(op, QobjEvo) for op in c_ops):
        # With QobjEvo, faster computation using Data is not used
        L = -1.0j * (spre(H) - spost(H))
        L += sum(lindblad_dissipator(c_op, chi=chi_)
                 for c_op, chi_ in zip(c_ops, chi))
        return L

    op_dims = H.dims
    op_shape = H.shape
    sop_dims = [[op_dims[0], op_dims[0]], [op_dims[1], op_dims[1]]]
    sop_shape = [np.prod(op_dims), np.prod(op_dims)]
    spI = _data.identity(op_shape[0])

    data = -1j * _data.kron(spI, H.data)
    data = _data.add(data, _data.kron(H.data.transpose(), spI), scale=1j)

    for c_op, chi_ in zip(c_ops, chi):
        c = c_op.data
        cd = c.adjoint()
        cdc = _data.matmul(cd, c)
        data = _data.add(data, _data.kron(c.conj(), c), np.exp(1j*chi_))
        data = _data.add(data, _data.kron(spI, cdc), -0.5)
        data = _data.add(data, _data.kron(cdc.transpose(), spI), -0.5)

    if data_only:
        return data
    else:
        return Qobj(data,
                    dims=sop_dims,
                    type='super',
                    superrep='super',
                    copy=False)


def lindblad_dissipator(a, b=None, data_only=False, chi=None):
    """
    Lindblad dissipator (generalized) for a single pair of collapse operators
    (a, b), or for a single collapse operator (a) when b is not specified:

    .. math::

        \\mathcal{D}[a,b]\\rho = a \\rho b^\\dagger -
        \\frac{1}{2}a^\\dagger b\\rho - \\frac{1}{2}\\rho a^\\dagger b

    Parameters
    ----------
    a : Qobj or QobjEvo
        Left part of collapse operator.

    b : Qobj or QobjEvo (optional)
        Right part of collapse operator. If not specified, b defaults to a.

    data_only :  bool [False]
        Return the data object instead of a Qobj

    Returns
    -------
    D : qobj, QobjEvo
        Lindblad dissipator superoperator.
    """
    from .cy.qobjevo import QobjEvo
    if b is None:
        b = a
    ad_b = a.dag() * b
    if chi:
        D = (
            spre(a) * spost(b.dag()) * np.exp(1j * chi)
            - 0.5 * spre(ad_b)
            - 0.5 * spost(ad_b)
        )
    else:
        D = spre(a) * spost(b.dag()) - 0.5 * spre(ad_b) - 0.5 * spost(ad_b)

    if isinstance(a, QobjEvo) or isinstance(b, QobjEvo):
        return D
    return D.data if data_only else D


@_map_over_compound_operators
def operator_to_vector(op):
    """
    Create a vector representation of a quantum operator given the matrix
    representation.  Note that QuTiP uses the _column-stacking_ convention,
    which may be different to what you expect.
    """
    return Qobj(stack_columns(op.data),
                dims=[op.dims, [1]],
                type='operator-ket',
                copy=False)


@_map_over_compound_operators
def vector_to_operator(op):
    """
    Create a matrix representation given a quantum operator in vector form.
    This is the inverse operation to `operator_to_vector`.
    """
    if not op.isoperket:
        raise TypeError("only defined for operator-kets")
    dims = op.dims[0]
    return Qobj(unstack_columns(op.data, (np.prod(dims[0]), np.prod(dims[1]))),
                dims=dims,
                copy=False)


def stack_columns(matrix):
    """
    Stack the columns in a data-layer type, useful for converting an operator
    into a superoperator representation.
    """
    if not isinstance(matrix, (_data.Data, np.ndarray)):
        raise TypeError(
            "input " + repr(type(matrix)) + " is not data-layer type"
        )
    if isinstance(matrix, np.ndarray):
        return matrix.ravel('F')[:, None]
    return _data.column_stack(matrix)


def unstack_columns(vector, shape=None):
    """
    Unstack the columns in a data-layer type back into a 2D shape, useful for
    converting an operator in vector form back into a regular operator.  If
    `shape` is not passed, the output operator will be assumed to be square.
    """
    if not isinstance(vector, (_data.Data, np.ndarray)):
        raise TypeError(
            "input " + repr(type(vector)) + " is not data-layer type"
        )
    if (
        (isinstance(vector, _data.Data) and vector.shape[1] != 1)
        or (isinstance(vector, np.ndarray)
            and ((vector.ndim == 2 and vector.shape[1] != 1)
                 or vector.ndim > 2))
    ):
        raise TypeError("input is not a single column")
    if shape is None:
        n = int(np.sqrt(vector.shape[0]))
        if n * n != vector.shape[0]:
            raise ValueError(
                "input cannot be made square, but no specific shape given"
            )
        shape = (n, n)
    if isinstance(vector, np.ndarray):
        return vector.reshape(shape, order='F')
    return _data.column_unstack(vector, shape[0])


def unstacked_index(size, index):
    """
    Convert an index into a column-stacked square operator with `size` rows and
    columns, into a pair of indices into the unstacked operator.
    """
    return index % size, index // size


def stacked_index(size, row, col):
    """
    Convert a pair of indices into a square operator of `size` into a single
    index into the column-stacked version of the operator.
    """
    return row + size*col


@_map_over_compound_operators
def spost(A):
    """
    Superoperator formed from post-multiplication by operator A

    Parameters
    ----------
    A : Qobj or QobjEvo
        Quantum operator for post multiplication.

    Returns
    -------
    super : Qobj or QobjEvo
        Superoperator formed from input qauntum object.
    """
    if not A.isoper:
        raise TypeError('Input is not a quantum operator')
    data = _data.kron(A.data.transpose(),
                      _data.identity(A.shape[0], dtype=type(A.data)))
    return Qobj(data,
                dims=[A.dims, A.dims],
                type='super',
                superrep='super',
                isherm=A._isherm,
                copy=False)


@_map_over_compound_operators
def spre(A):
    """Superoperator formed from pre-multiplication by operator A.

    Parameters
    ----------
    A : Qobj or QobjEvo
        Quantum operator for pre-multiplication.

    Returns
    --------
    super :Qobj or QobjEvo
        Superoperator formed from input quantum object.
    """
    if not A.isoper:
        raise TypeError('Input is not a quantum operator')
    data = _data.kron(_data.identity(A.shape[0], dtype=type(A.data)), A.data)
    return Qobj(data,
                dims=[A.dims, A.dims],
                type='super',
                superrep='super',
                isherm=A._isherm,
                copy=False)


def _drop_projected_dims(dims):
    """
    Eliminate subsystems that has been collapsed to only one state due to
    a projection.
    """
    return [d for d in dims if d != 1]


def sprepost(A, B):
    """
    Superoperator formed from pre-multiplication by A and post-multiplication
    by B.

    Parameters
    ----------
    A : Qobj or QobjEvo
        Quantum operator for pre-multiplication.

    B : Qobj or QobjEvo
        Quantum operator for post-multiplication.

    Returns
    --------
    super : Qobj or QobjEvo
        Superoperator formed from input quantum objects.
    """
    from .cy.qobjevo import QobjEvo
    if (isinstance(A, QobjEvo) or isinstance(B, QobjEvo)):
        return spre(A) * spost(B)
    dims = [[_drop_projected_dims(A.dims[0]),
             _drop_projected_dims(B.dims[1])],
            [_drop_projected_dims(A.dims[1]),
             _drop_projected_dims(B.dims[0])]]
    return Qobj(_data.kron(B.data.transpose(), A.data),
                dims=dims,
                type='super',
                superrep='super',
                isherm=A._isherm and B._isherm,
                copy=False)


def reshuffle(q_oper):
    """
    Column-reshuffles a ``type="super"`` Qobj.
    """
    if q_oper.type not in ('super', 'operator-ket'):
        raise TypeError("Reshuffling is only supported on type='super' "
                        "or type='operator-ket'.")
    # How many indices are there, and how many subsystems can we decompose
    # each index into?
    n_indices = len(q_oper.dims[0])
    n_subsystems = len(q_oper.dims[0][0])
    # Generate a list of lists (lol) that represents the permutation order we
    # need. It's easiest to do so if we make an array, then turn it into a lol
    # by using map(list, ...). That array is generated by using reshape and
    # transpose to turn an array like [a, b, a, b, ..., a, b] into one like
    # [a, a, ..., a, b, b, ..., b].
    perm_idxs = map(list,
                    np.arange(n_subsystems * n_indices)[
                        np.arange(n_subsystems * n_indices).reshape(
                            (n_indices, n_subsystems)).T.flatten()
                    ].reshape((n_subsystems, n_indices))
                    )
    return q_oper.permute(list(perm_idxs))
