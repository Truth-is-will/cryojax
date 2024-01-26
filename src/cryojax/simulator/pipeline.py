"""
Image formation models.
"""

from __future__ import annotations

__all__ = ["ImagePipeline", "SuperpositionPipeline"]

from functools import partial
from typing import Optional
from typing_extensions import override

import equinox as eqx
import jax
import jax.numpy as jnp
from jaxtyping import PRNGKeyArray
from equinox import Module

from .specimen import AbstractSpecimen, AbstractConformation
from .pose import AbstractPose
from .scattering import AbstractScatteringMethod
from .instrument import Instrument
from .detector import NullDetector
from .ice import AbstractIce, NullIce
from ..image import rfftn, irfftn
from ..image.operators import Filter, Mask
from ..core import field
from ..typing import ComplexImage, Image


class ImagePipeline(Module):
    """
    Base class for an imaging model.

    Call an ``ImagePipeline``'s ``render`` and ``sample``,
    routines.

    Attributes
    ----------
    specimen :
        The ensemble from which to render images.
    scattering :
        The scattering model.
    instrument :
        The abstraction of the electron microscope.
    solvent :
        The solvent around the specimen.
    filter :
        A filter to apply to the image.
    mask :
        A mask to apply to the image.
    """

    specimen: AbstractSpecimen = field()
    scattering: AbstractScatteringMethod = field()
    instrument: Instrument = field(default_factory=Instrument)
    solvent: AbstractIce = field(default_factory=NullIce)

    filter: Optional[Filter] = field(default=None)
    mask: Optional[Mask] = field(default=None)

    def render(
        self,
        *,
        view_cropped: bool = True,
        get_real: bool = True,
        normalize: bool = False,
    ) -> Image:
        """
        Render an image of a Specimen without any stochasticity.

        Namely, do not sample from the ``Ice`` and ``Detector``
        models.

        Parameters
        ----------
        view_cropped : `bool`, optional
            If ``True``, view the cropped image.
            If ``view_cropped = False``, ``ImagePipeline.filter``,
            ``ImagePipeline.mask``, and normalization with
            ``normalize = True`` are not applied.
        get_real : `bool`, optional
            If ``True``, return the image in real space.
        normalize : `bool`, optional
            If ``True``, normalize the image to mean zero
            and standard deviation 1.
        """
        # Scattering the specimen to the exit plane
        image_at_exit_plane = self.instrument.scatter_to_exit_plane(
            self.specimen, self.scattering
        )
        # Measure the image at the detector plane
        image_at_detector_plane = self.instrument.propagate_to_detector_plane(
            image_at_exit_plane,
            self.scattering,
            defocus_offset=self.specimen.pose.offset_z,
        )

        return self._get_final_image(
            image_at_detector_plane,
            view_cropped=view_cropped,
            get_real=get_real,
            normalize=normalize,
        )

    def sample(
        self,
        key: PRNGKeyArray,
        *,
        view_cropped: bool = True,
        get_real: bool = True,
        normalize: bool = False,
    ) -> Image:
        """
        Sample an image from a realization of the ``Ice`` and
        ``Detector`` models.

        Parameters
        ----------
        key :
            The random number generator key.
        view_cropped : `bool`, optional
            If ``True``, view the cropped image.
            If ``view_cropped = False``, ``ImagePipeline.filter``,
            ``ImagePipeline.mask``, and normalization with
            ``normalize = True`` are not applied.
        get_real : `bool`, optional
            If ``True``, return the image in real space.
        normalize : `bool`, optional
            If ``True``, normalize the image to mean zero
            and standard deviation 1.
        """
        idx = 0  # Keep track of number of stochastic models
        if not isinstance(self.solvent, NullIce) and not isinstance(
            self.instrument.detector, NullDetector
        ):
            keys = jax.random.split(key)
        else:
            keys = jnp.expand_dims(key, axis=0)
        # Scatter the specimen to the exit plane
        image_at_exit_plane = self.instrument.scatter_to_exit_plane(
            self.specimen, self.scattering
        )
        if not isinstance(self.solvent, NullIce):
            # Measure the image at the detector plane with the solvent
            image_at_detector_plane = (
                self.instrument.propagate_to_detector_plane_with_solvent(
                    keys[idx],
                    image_at_exit_plane,
                    self.solvent,
                    self.scattering,
                    defocus_offset=self.specimen.pose.offset_z,
                )
            )
            idx += 1
        else:
            # ... otherwise, just measure the specimen at the detector plane
            image_at_detector_plane = (
                self.instrument.propagate_to_detector_plane(
                    image_at_exit_plane,
                    self.scattering,
                    defocus_offset=self.specimen.pose.offset_z,
                )
            )
        # Finally, measure the detector readout
        detector_readout = self.instrument.measure_detector_readout(
            keys[idx], image_at_detector_plane, self.scattering
        )

        return self._get_final_image(
            detector_readout,
            view_cropped=view_cropped,
            get_real=get_real,
            normalize=normalize,
        )

    def __call__(
        self,
        key: Optional[PRNGKeyArray] = None,
        *,
        view_cropped: bool = True,
        get_real: bool = True,
        normalize: bool = False,
    ) -> Image:
        """Sample an image with the noise models or render an image
        without them.
        """
        if key is None:
            return self.render(
                view_cropped=view_cropped,
                get_real=get_real,
                normalize=normalize,
            )
        else:
            return self.sample(
                key,
                view_cropped=view_cropped,
                get_real=get_real,
                normalize=normalize,
            )

    def crop_and_apply_operators(
        self,
        image: ComplexImage,
        *,
        get_real: bool = True,
        normalize: bool = False,
    ) -> Image:
        """
        Return an image postprocessed with filters, cropping, and masking
        in either real or fourier space.
        """
        manager = self.scattering.manager
        # Apply filter
        if self.filter is not None:
            image = self.filter(image)
        # Crop and apply mask
        if self.mask is None and manager.padded_shape == manager.shape:
            # ... if there are no masks and we don't need to crop,
            # minimize moving back and forth between real and fourier space
            if normalize:
                image = manager.normalize_image(
                    image, is_real=False, shape_in_real_space=manager.shape
                )
            return irfftn(image, s=manager.shape) if get_real else image
        else:
            # ... otherwise, inverse transform, mask, crop, and normalize
            image = irfftn(image, s=manager.padded_shape)
            if self.mask is not None:
                image = self.mask(image)
            image = manager.crop_to_shape(image)
            if normalize:
                image = manager.normalize_image(image, is_real=True)
            return image if get_real else rfftn(image)

    def _get_final_image(
        self,
        image: ComplexImage,
        *,
        view_cropped: bool = True,
        get_real: bool = True,
        normalize: bool = False,
    ) -> Image:
        manager = self.scattering.manager
        if view_cropped:
            return self.crop_and_apply_operators(
                image,
                get_real=get_real,
                normalize=normalize,
            )
        else:
            return irfftn(image, s=manager.padded_shape) if get_real else image


class SuperpositionPipeline(ImagePipeline):
    """
    Compute an image from a superposition of states in
    ``Ensemble``. This assumes that either ``Ensemble.Pose``
    and/or ``Ensemble.conformation`` has a batch dimension.

    This class can be used to compute a micrograph, where there
    are many specimen in the field of view. Or it can be used to
    compute an image from ``Assembly.subunits``.
    """

    @partial(
        jax.jit, static_argnames=["view_cropped", "get_real", "normalize"]
    )
    @override
    def sample(
        self,
        key: PRNGKeyArray,
        *,
        view_cropped: bool = True,
        get_real: bool = True,
        normalize: bool = False,
    ) -> Image:
        """Sample the superposition of states from the stochastic models."""
        if not isinstance(self.solvent, NullIce):
            raise NotImplementedError(
                "The SuperpositionPipeline does not currently support sampling from the solvent model."
            )
        # Get the superposition of images
        image_at_detector_plane = self.render(
            view_cropped=False, get_real=False
        )
        # Sample from the detector model
        detector_readout = self.instrument.measure_detector_readout(
            key, image_at_detector_plane, self.scattering
        )

        return self._get_final_image(
            detector_readout,
            view_cropped=view_cropped,
            get_real=get_real,
            normalize=normalize,
        )

    @override
    def render(
        self,
        *,
        view_cropped: bool = True,
        get_real: bool = True,
        normalize: bool = False,
    ) -> Image:
        """Render the superposition of states in the Ensemble."""
        # Setup vmap over the pose and conformation
        is_vmap = lambda x: isinstance(x, (AbstractPose, AbstractConformation))
        to_vmap = jax.tree_util.tree_map(is_vmap, self, is_leaf=is_vmap)
        vmap, novmap = eqx.partition(self, to_vmap)
        # Compute all images and sum
        compute_image = lambda model: super(type(model), model).render(
            view_cropped=False, get_real=False
        )
        # ... vmap to compute a stack of images to superimpose
        compute_stack = jax.vmap(
            lambda vmap, novmap: compute_image(eqx.combine(vmap, novmap)),
            in_axes=(0, None),
        )
        # ... sum over the stack of images and jit
        compute_stack_and_sum = jax.jit(
            lambda vmap, novmap: jnp.sum(
                compute_stack(vmap, novmap),
                axis=0,
            )
        )
        # ... compute the superposition
        image = compute_stack_and_sum(vmap, novmap)

        return self._get_final_image(
            image,
            view_cropped=view_cropped,
            get_real=get_real,
            normalize=normalize,
        )
