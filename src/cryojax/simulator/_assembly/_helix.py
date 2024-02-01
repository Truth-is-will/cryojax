"""
Abstraction of a helical polymer.
"""

from typing import Union, Optional
from jaxtyping import Array, Float
from functools import cached_property, partial
from equinox import field

import jax
import jax.numpy as jnp

from .._specimen import AbstractSpecimen
from .._pose import AbstractPose, EulerPose
from .._conformation import AbstractConformation
from ._assembly import AbstractAssembly

from ...typing import Real_, RealVector


class Helix(AbstractAssembly, strict=True):
    """
    Abstraction of a helical polymer.

    This class assembles a helix from a subunit.
    See the ``AbstractAssembly`` base class for more information.

    The screw axis is taken to be in the center of the
    image, pointing out-of-plane (i.e. along the z direction).

    Attributes
    ----------
    subunit :
        The helical subunit. It is important to set the the initial pose
        of the initial subunit here. This is in the center of mass frame
        of the helix with the screw axis pointing in the z-direction.
    rise :
        The helical rise. This has dimensions of length.
    twist :
        The helical twist, given in degrees if
        ``degrees = True`` and radians otherwise.
    pose :
        The center of mass pose of the helix.
    conformation :
        The conformation of `subunit` at each lattice site.
        This can either be a fixed set of conformations or a function
        that computes conformations based on the lattice positions.
        In either case, the `Array` should be shape `(n_start*n_subunits_per_start,)`.
    n_start :
        The start number of the helix. By default, ``1``.
    n_subunits_per_start :
        The number of subunits each helical strand.
        By default, ``1``.
    degrees :
        Whether or not the helical twist is given in
        degrees. By default, ``True``.
    """

    subunit: AbstractSpecimen
    rise: Real_ = field(converter=jnp.asarray)
    twist: Real_ = field(converter=jnp.asarray)

    pose: AbstractPose
    conformation: Optional[AbstractConformation]

    n_subunits: int = field(static=True)
    n_start: int = field(static=True)
    degrees: bool = field(static=True)

    def __init__(
        self,
        subunit: AbstractSpecimen,
        rise: Real_,
        twist: Real_,
        pose: Optional[AbstractPose] = None,
        conformation: Optional[AbstractConformation] = None,
        n_start: int = 1,
        n_subunits: int = 1,
        degrees: bool = True,
    ):
        self.subunit = subunit
        self.pose = pose or EulerPose()
        self.rise = rise
        self.twist = twist
        self.conformation = conformation
        self.n_start = n_start
        self.n_subunits = n_subunits
        self.degrees = degrees

    def __check_init__(self):
        if self.n_subunits % self.n_start != 0:
            raise AttributeError(
                "The number of subunits must be a multiple of the helical start number."
            )

    @cached_property
    def positions(self) -> Float[Array, "n_subunits 3"]:
        """Get the helical lattice positions in the center of mass frame."""
        return compute_helical_lattice_positions(
            self.rise,
            self.twist,
            self.subunit.pose.offset,
            n_start=self.n_start,
            n_subunits_per_start=self.n_subunits // self.n_start,
            degrees=self.degrees,
        )

    @cached_property
    def rotations(self) -> Float[Array, "n_subunits 3 3"]:
        """
        Get the helical lattice rotations in the center of mass frame.

        These are rotations of the initial subunit.
        """
        return compute_helical_lattice_rotations(
            self.twist,
            self.subunit.pose.rotation.as_matrix(),
            n_start=self.n_start,
            n_subunits_per_start=self.n_subunits // self.n_start,
            degrees=self.degrees,
        )


def compute_helical_lattice_positions(
    rise: Real_,
    twist: Real_,
    initial_displacement: Float[Array, "3"],
    n_start: int = 1,
    n_subunits_per_start: Optional[int] = None,
    *,
    degrees: bool = True,
) -> Float[Array, "n_start*n_subunits_per_start 3"]:
    """
    Compute the lattice points of a helix for a given
    rise, twist, radius, and start number.

    Arguments
    ---------
    rise : `Real_` or `RealVector`, shape `(n_subunits,)`
        The helical rise.
    twist : `Real_` or `RealVector`, shape `(n_subunits,)`
        The helical twist.
    initial_displacement : `Array`, shape `(3,)`
        The initial position vector of the first subunit, in
        the center of mass frame of the helix.
        The xy values are an in-plane displacement from
        the screw axis, and the z value is an offset from the
        first subunit's position.
    n_start :
        The start number of the helix.
    n_subunits_per_start :
        The number of subunits in the assembly for
        a single helix. The total number of subunits
        is really equal to ``n_start * n_subunits``.
        By default, ``2 * jnp.pi / twist``.
    degrees :
        Whether or not the angular parameters
        are given in degrees or radians.

    Returns
    -------
    subunit_positions : shape `(n_start*n_subunits_per_start, 3)`
        The helical lattice positions.
    """
    # Convert to radians
    if degrees:
        twist = jnp.deg2rad(twist)
    # If the number of subunits is not given, compute for one helical turn
    if n_subunits_per_start is None:
        n_subunits_per_start = abs(int(2 * jnp.pi / twist))

    # Coordinate transformation to get subunit positions in a single sub-helix
    def compute_ith_subunit_position_in_subhelix(i, theta, dz, r_0, N):
        # ... define rotation about the screw axis
        c, s = jnp.cos(i * theta), jnp.sin(i * theta)
        R_i = jnp.array(((c, s, 0), (-s, c, 0), (0, 0, 1)), dtype=float)
        # ... transform to the ith position
        r_i = R_i @ r_0 + i * jnp.asarray((0, 0, dz), dtype=float)
        # ... center positions of subunit in z
        return r_i - jnp.asarray(
            (0.0, 0.0, dz * jnp.asarray(N - 1, dtype=float) / 2), dtype=float
        )

    # ... function to get positions of all subunits
    compute_subunit_positions_in_subhelix = jax.vmap(
        compute_ith_subunit_position_in_subhelix, in_axes=[0, None, None, None]
    )
    # ... get indices of subunits along sub-helix
    subunit_indices = jnp.arange(n_subunits_per_start, dtype=float)
    # ... compute positions of subunits in sub-helix
    subunit_positions_in_subhelix = compute_subunit_positions_in_subhelix(
        subunit_indices,
        twist,
        rise,
        initial_displacement,
        n_subunits_per_start,
    )

    # Now, transform this single sub-helix into all sub-helices, related by C_n
    # symmetry
    def compute_helix_subunit_positions_per_start(symmetry_angle, r):
        # ... rotate the sub-helix around the screw axis to a different sub-helix
        c_n, s_n = jnp.cos(symmetry_angle), jnp.sin(symmetry_angle)
        R_n = jnp.array(
            ((c_n, s_n, 0), (-s_n, c_n, 0), (0, 0, 1)), dtype=float
        )
        return (R_n @ r.T).T

    # ... function to rotate entire sub-helix around the screw axis
    compute_helix_subunit_positions = jax.vmap(
        compute_helix_subunit_positions_per_start, in_axes=[0, None]
    )
    # ... compute symmetry angles relating first sub-helix to all other sub-helices
    symmetry_angles = jnp.array(
        [2 * jnp.pi * n / n_start for n in range(n_start)]
    )
    # ... finally, get all subunit positions!
    subunit_positions = compute_helix_subunit_positions(
        symmetry_angles, subunit_positions_in_subhelix
    ).reshape((n_start * n_subunits_per_start, 3))

    return subunit_positions


def compute_helical_lattice_rotations(
    twist: Union[Real_, RealVector],
    initial_rotation: Float[Array, "3 3"],
    n_start: int = 1,
    n_subunits_per_start: Optional[int] = None,
    *,
    degrees: bool = True,
) -> Float[Array, "n_start*n_subunits_per_start 3"]:
    """
    Compute the relative rotations of subunits on a
    helical lattice, parameterized by the
    rise, twist, start number, and an initial rotation.

    Arguments
    ---------
    twist : `Real_` or `RealVector`, shape `(n_subunits,)`
        The helical twist.
    initial_rotation : `Array`, shape `(3, 3)`
        The initial rotation of the first subunit.
    n_start :
        The start number of the helix.
    n_subunits_per_start :
        The number of subunits in the assembly for
        a single helix. The total number of subunits
        is really equal to ``n_start * n_subunits``.
        By default, ``2 * jnp.pi / twist``.
    degrees :
        Whether or not the angular parameters
        are given in degrees or radians.

    Returns
    -------
    rotations : shape `(n_start*n_subunits_per_start, 3, 3)`
        The relative rotations between subunits
        on the helical lattice.
    """
    # Convert to radians
    if degrees:
        twist = jnp.deg2rad(twist)
    # If the number of subunits is not given, compute for one helix
    if n_subunits_per_start is None:
        n_subunits_per_start = int(2 * jnp.pi / twist)
    # Rotational symmetry between helices due to the start number
    symmetry_angles = jnp.array(
        [2 * jnp.pi * n / n_start for n in range(n_start)]
    )

    def compute_helix_rotations(symmetry_angle):
        """
        Get  coordinates for a given helix, where
        the x and y coordinates are rotated by an angle.
        """
        T_0 = initial_rotation
        # Define rotation about the screw axis
        c, s = jnp.cos(twist), jnp.sin(twist)
        R = jnp.array(((c, s, 0), (-s, c, 0), (0, 0, 1)), dtype=float)

        # Coordinate transformation between subunits
        def f(carry, x):
            y = R @ carry
            return y, y

        _, T = jax.lax.scan(f, T_0, None, length=n_subunits_per_start - 1)
        T = jnp.insert(T, 0, T_0, axis=0)
        # Transformation between helical strands from start-number
        c_n, s_n = jnp.cos(symmetry_angle), jnp.sin(symmetry_angle)
        R_n = jnp.array(
            ((c_n, s_n, 0), (-s_n, c_n, 0), (0, 0, 1)), dtype=float
        )
        return jnp.einsum("ij,njk->nik", R_n, T)

    rotations = jax.vmap(compute_helix_rotations)(symmetry_angles)
    return rotations.reshape((n_start * n_subunits_per_start, 3, 3))
