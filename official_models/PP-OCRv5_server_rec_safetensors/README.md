---
license: apache-2.0
library_name: PaddleOCR
language:
- en
- zh
pipeline_tag: image-to-text
tags:
- OCR
- PaddlePaddle
- PaddleOCR
- textline_recognition
---

# PP-OCRv5_server_rec

## Introduction

PP-OCRv5_server_rec is one of the PP-OCRv5_rec that are the latest generation text line recognition models developed by PaddleOCR team. It aims to efficiently and accurately support the recognition of four major languages—Simplified Chinese, Traditional Chinese, English, and Japanese—as well as complex text scenarios such as handwriting, vertical text, pinyin, and rare characters using a single model. The key accuracy metrics are as follow:

| Handwritten Chinese | Handwritten English | Printed Chinese | Printed English | Traditional Chinese | Ancient Text | Japanese | General Scenario | Pinyin | Rotation | Distortion | Artistic Text | Average | 
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.5807 | 0.5806 | 0.9013 | 0.8679 | 0.7472 | 0.6039 | 0.7372 | 0.5946 | 0.8384 | 0.7435 | 0.9314 | 0.6397 | 0.8401 |

**Note**: If any character (including punctuation) in a line was incorrect, the entire line was marked as wrong. This ensures higher accuracy in practical applications.

## Model Usage

```python
import requests
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForTextRecognition

model_path="PaddlePaddle/PP-OCRv5_server_rec_safetensors"
model = AutoModelForTextRecognition.from_pretrained(model_path, device_map="auto")
image_processor = AutoImageProcessor.from_pretrained(model_path)

image = Image.open(requests.get("https://paddle-model-ecology.bj.bcebos.com/paddlex/imgs/demo_image/general_ocr_rec_001.png", stream=True).raw).convert("RGB")
inputs = image_processor(images=image, return_tensors="pt").to(model.device)
outputs = model(**inputs)

results = image_processor.post_process_text_recognition(outputs)

for result in results:
    print(result)
```