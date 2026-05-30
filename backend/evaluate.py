from itertools import permutations

def box_area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])

def select_fixed3(boxes, confs, image_size):
    width, height = image_size
    candidates = []
    for box, conf in zip(boxes, confs):
        x1, y1, x2, y2 = box
        bw, bh = x2 - x1, y2 - y1
        area = bw * bh
        if bw < 22 or bh < 22: continue
        if area < 550: continue
        if bw > width * 0.38 or bh > height * 0.38: continue
        if bw < 10 or bh < 10: continue
        candidates.append((box, conf))
    if len(candidates) < 3:
        candidates = sorted(zip(boxes, confs), key=lambda x: x[1], reverse=True)[:5]
        candidates = [(b, c) for b, c in candidates if box_area(b) >= 300][:3]
    candidates = sorted(candidates, key=lambda x: x[1], reverse=True)[:3]
    if len(candidates) < 3:
        candidates = sorted(zip(boxes, confs), key=lambda x: x[1], reverse=True)[:3]
    boxes_out = [b for b, _ in candidates]
    confs_out = [c for _, c in candidates]
    reason = f"selected {len(boxes_out)}/{len(boxes)}"
    return boxes_out, confs_out, reason
