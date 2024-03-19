import pytest

import equinox as eqx
import jax.numpy as jnp
import numpy as np
import cryojax.simulator as cs
from cryojax.rotations import SO3


def test_default_pose_arguments():
    euler = cs.EulerAnglePose()
    quat = cs.QuaternionPose()
    axis_angle = cs.AxisAnglePose()
    np.testing.assert_allclose(euler.rotation.as_matrix(), quat.rotation.as_matrix())
    np.testing.assert_allclose(
        euler.rotation.as_matrix(), axis_angle.rotation.as_matrix()
    )


def test_translation_agreement():
    rotation = SO3(jnp.asarray((1.0, 0.0, 0.0, 0.0)))
    offset = jnp.asarray((0.0, -1.4, 4.5))
    quat = cs.QuaternionPose.from_rotation_and_translation(rotation, offset)
    axis_angle = cs.AxisAnglePose.from_rotation_and_translation(rotation, offset)
    np.testing.assert_allclose(
        quat.rotation.as_matrix(), axis_angle.rotation.as_matrix()
    )
    np.testing.assert_allclose(quat.offset_in_angstroms, axis_angle.offset_in_angstroms)


def test_pose_conversion():
    wxyz = jnp.asarray((1.0, 2.0, 3.0, 0.5))
    rotation = SO3(wxyz).normalize()
    quat = cs.QuaternionPose.from_rotation(rotation)
    euler = cs.EulerAnglePose.from_rotation(rotation)
    axis_angle = cs.AxisAnglePose.from_rotation(rotation)
    np.testing.assert_allclose(quat.rotation.as_matrix(), euler.rotation.as_matrix())
    np.testing.assert_allclose(
        quat.rotation.as_matrix(), axis_angle.rotation.as_matrix()
    )


def test_default_pose_images(noiseless_model):
    euler = cs.EulerAnglePose()
    quat = cs.QuaternionPose()

    model_euler = eqx.tree_at(lambda m: m.specimen.pose, noiseless_model, euler)
    model_quat = eqx.tree_at(lambda m: m.specimen.pose, noiseless_model, quat)
    np.testing.assert_allclose(model_euler.render(), model_quat.render())


def test_axis_angle_euler_agreement():
    angle = 2.0
    angle_in_radians = jnp.deg2rad(angle)
    rotation_x = SO3.from_x_radians(angle_in_radians)
    rotation_y = SO3.from_y_radians(angle_in_radians)
    rotation_z = SO3.from_z_radians(angle_in_radians)
    aa_x = cs.AxisAnglePose(euler_vector=(angle, 0.0, 0.0))
    aa_y = cs.AxisAnglePose(euler_vector=(0.0, angle, 0.0))
    aa_z = cs.AxisAnglePose(euler_vector=(0.0, 0.0, angle))
    np.testing.assert_allclose(rotation_x.as_matrix(), aa_x.rotation.as_matrix())
    np.testing.assert_allclose(rotation_y.as_matrix(), aa_y.rotation.as_matrix())
    np.testing.assert_allclose(rotation_z.as_matrix(), aa_z.rotation.as_matrix())


def test_euler_angle_conversion():
    phi, theta, psi = 2.0, -15.0, 40.0
    pose = cs.EulerAnglePose(view_phi=phi, view_theta=theta, view_psi=psi)
    converted_pose = cs.EulerAnglePose.from_rotation(pose.rotation)
    np.testing.assert_allclose(
        np.asarray((phi, theta, psi)),
        np.asarray(
            (
                converted_pose.view_phi,
                converted_pose.view_theta,
                converted_pose.view_psi,
            )
        ),
    )
