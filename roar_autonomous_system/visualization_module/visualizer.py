import logging
from roar_autonomous_system.utilities_module.data_structures_models import Transform, Location, Rotation
from roar_autonomous_system.utilities_module.camera_models import Camera
import numpy as np
import cv2
from roar_autonomous_system.agent_module.agent import Agent


class Visualizer:
    def __init__(self, agent: Agent):
        self.logger = logging.getLogger(__name__)
        self.agent = agent

    def visualize_waypoint(self, waypoint_transform: Transform):
        coord = self.calculate_img_pos(waypoint_transform=waypoint_transform,
                                       camera=self.agent.front_depth_camera)
        img = self.agent.front_rgb_camera.data.copy()
        start_point = (400, 600)
        end_point = (coord[0], coord[1])
        color = (0, 255, 0)
        thickness = 2
        img = cv2.arrowedLine(img, start_point, end_point,
                              color, thickness)
        cv2.imshow("Next Waypoint", img)
        cv2.waitKey(1)

    def calculate_img_pos(self,
                          waypoint_transform: Transform, camera: Camera):
        """
        Calculate the 2D image coordinate from 3D world space

        Args:
            camera:
            waypoint_transform: Desired point in 3D world space

        Returns:
            Array if integers [X, Y, depth]

        """
        waypoint_location = waypoint_transform.location.to_array()
        waypoint_location = np.concatenate([waypoint_location, [1]])  # 4 x 1 array [X, Y, Z, 1]
        veh_cam_matrix = self.agent.front_depth_camera.transform.get_matrix()  # 4 x 4
        # veh_cam_matrix[1][3] = veh_cam_matrix[0][3]
        world_veh_matrix = self.agent.vehicle.transform.get_matrix()  # 4 x 4

        # cam_transform = Transform(
        #     location=Location(x=self.agent.vehicle.transform.location.x, y=self.agent.vehicle.transform.location.y - 1.6, z=self.agent.vehicle.transform.location.z),
        #     rotation=self.agent.vehicle.transform.rotation
        # )
        # print(cam_transform)
        # cam_world = cam_transform.get_matrix().T

        cam_world = np.linalg.inv(np.dot(world_veh_matrix, veh_cam_matrix))

        cords_xyz = cam_world @ waypoint_location

        cords_y_minus_z_x = np.array([cords_xyz[1], -cords_xyz[2], cords_xyz[0]])
        raw_p2d = camera.intrinsics_matrix @ cords_y_minus_z_x
        img_cords = np.array([raw_p2d[0] / raw_p2d[2], raw_p2d[1] / raw_p2d[2], raw_p2d[2]])
        return img_cords.astype(np.int64)

    def visualize(self, next_waypoint_transform: Transform):
        """
        This function will allow multiple objects to be drawn on here.

        Currently implemented are
        1. Next Waypoint


        Args:
            next_waypoint_transform: location & rotation of the next waypoint

        Returns:

        """
        next_waypoint_cam_pos = self.calculate_img_pos(waypoint_transform=next_waypoint_transform,
                                                       camera=self.agent.front_depth_camera)
        img = self.agent.front_rgb_camera.data.copy()

        start_point = (400, 600)

        img = cv2.arrowedLine(img=img,
                              pt1=start_point,
                              pt2=(next_waypoint_cam_pos[0], next_waypoint_cam_pos[1]),
                              color=(0, 255, 0),
                              thickness=2)
        cv2.imshow("Visualization", img)
        cv2.waitKey(1)

    @classmethod
    def visualize_semantic_segmentation(cls, semantic_segmetation):
        """

        Args:
            semantic_segmetation: Width x Height x 3 array with white = obstacles, black = ground, blue = sky

        Returns:

        """

        if semantic_segmetation is not None:
            cv2.imshow("Semantic Segmentation", semantic_segmetation)
            cv2.waitKey(1)
