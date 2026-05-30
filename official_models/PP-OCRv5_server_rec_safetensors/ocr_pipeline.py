import requests
from PIL import Image
import cv2
import numpy as np
from typing import List
import copy
from transformers import AutoImageProcessor, AutoModelForObjectDetection, AutoModelForTextRecognition


class CropByQuadPoints:
    def __call__(self, img: np.ndarray, quad_points: List[list]) -> List[dict]:
        """
        Call method to crop images based on detection boxes.

        Args:
            img (nd.ndarray): The input image.
            quad_points (list[list]): List of detection points.

        Returns:
            list[dict]: A list of dictionaries containing cropped images and their sizes.
        """
        dt_boxes = np.array(quad_points)
        output_list = []
        for bno in range(len(dt_boxes)):
            tmp_box = copy.deepcopy(dt_boxes[bno])
            img_crop = self.get_minarea_rect_crop(img, tmp_box)
            output_list.append(img_crop)

        return output_list

    def get_minarea_rect_crop(self, img: np.ndarray, points: np.ndarray) -> np.ndarray:
        """
        Get the minimum area rectangle crop from the given image and points.

        Args:
            img (np.ndarray): The input image.
            points (np.ndarray): A list of points defining the shape to be cropped.

        Returns:
            np.ndarray: The cropped image with the minimum area rectangle.
        """
        bounding_box = cv2.minAreaRect(np.array(points).astype(np.int32))
        points = sorted(list(cv2.boxPoints(bounding_box)), key=lambda x: x[0])

        index_a, index_b, index_c, index_d = 0, 1, 2, 3
        if points[1][1] > points[0][1]:
            index_a = 0
            index_d = 1
        else:
            index_a = 1
            index_d = 0
        if points[3][1] > points[2][1]:
            index_b = 2
            index_c = 3
        else:
            index_b = 3
            index_c = 2

        box = [points[index_a], points[index_b], points[index_c], points[index_d]]
        crop_img = self.get_rotate_crop_image(img, np.array(box))
        return crop_img

    def get_rotate_crop_image(self, img: np.ndarray, points: list) -> np.ndarray:
        """
        Crop and rotate the input image based on the given four points to form a perspective-transformed image.

        Args:
            img (np.ndarray): The input image array.
            points (list): A list of four 2D points defining the crop region in the image.

        Returns:
            np.ndarray: The transformed image array.
        """
        assert len(points) == 4, "shape of points must be 4*2"
        img_crop_width = int(
            max(
                np.linalg.norm(points[0] - points[1]),
                np.linalg.norm(points[2] - points[3]),
            )
        )
        img_crop_height = int(
            max(
                np.linalg.norm(points[0] - points[3]),
                np.linalg.norm(points[1] - points[2]),
            )
        )
        pts_std = np.float32(
            [
                [0, 0],
                [img_crop_width, 0],
                [img_crop_width, img_crop_height],
                [0, img_crop_height],
            ]
        )
        M = cv2.getPerspectiveTransform(points, pts_std)
        dst_img = cv2.warpPerspective(
            img,
            M,
            (img_crop_width, img_crop_height),
            borderMode=cv2.BORDER_REPLICATE,
            flags=cv2.INTER_CUBIC,
        )
        dst_img_height, dst_img_width = dst_img.shape[0:2]
        if dst_img_height * 1.0 / dst_img_width >= 1.5:
            dst_img = np.rot90(dst_img)
        return dst_img


if __name__ == "__main__":
    det_model_path = "PaddlePaddle/PP-OCRv5_server_det_safetensors"
    rec_model_path = "PaddlePaddle/PP-OCRv5_server_rec_safetensors"
    input_image = "https://paddle-model-ecology.bj.bcebos.com/paddlex/imgs/demo_image/general_ocr_001.png"

    # ========== 1. Load text detection model ==========
    det_model = AutoModelForObjectDetection.from_pretrained(det_model_path, device_map="auto")
    det_processor = AutoImageProcessor.from_pretrained(det_model_path, backend="torchvision", limit_side_len=64, limit_type="min")

    # ========== 2. Load text recognition model  ==========
    rec_model = AutoModelForTextRecognition.from_pretrained(rec_model_path, device_map="auto")
    rec_processor = AutoImageProcessor.from_pretrained(rec_model_path, backend="torchvision")

    # ========== 3. Load image ==========
    image = Image.open(requests.get(input_image, stream=True).raw).convert("RGB")

    # ========== 4. Detect text blocks ==========
    det_inputs = det_processor(images=image, return_tensors="pt").to(det_model.device)
    det_outputs = det_model(**det_inputs)
    det_results = det_processor.post_process_object_detection(det_outputs, target_sizes=det_inputs["target_sizes"])
    boxes = det_results[0]["boxes"]

    # ========== 5. Crop all text regions ==========
    crop_utils = CropByQuadPoints()
    image_np = np.array(image)
    quad_points = boxes.cpu().numpy().tolist()
    cropped_images = crop_utils(image_np, quad_points)

    # ========== 6. Recognize text ==========
    rec_inputs = rec_processor(images=cropped_images, return_tensors="pt").to(rec_model.device)
    rec_outputs = rec_model(**rec_inputs)
    rec_results = rec_processor.post_process_text_recognition(rec_outputs)

    # ========== 7. Output the results ==========
    for i in range(len(rec_results)):
        rec_results[i]["box"] = boxes[i]
        print(rec_results[i])
