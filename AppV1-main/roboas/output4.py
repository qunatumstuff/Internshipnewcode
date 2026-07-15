import math
import os
import cv2
import numpy as np
import pyrealsense2 as rs
from ultralytics import YOLO
from mcp.server.fastmcp import FastMCP
import threading
import base64
import time


# -----------------------------------------------------------------------------
# 1. Global State Variables (Shared between Vision Thread and MCP Server)
# -----------------------------------------------------------------------------
current_rgb_frame = None
current_target_class = None  # e.g., "cup", "bottle", "apple"
latest_3d_coords = {"x": 0.0, "y": 0.0, "z": 0.0}

# Initialize F                                                                                                                                                          1`astMCP Server
mcp = FastMCP("TIEFA_Module_B_Vision")

model=YOLO("best (12).pt")
segment=YOLO("best (11).pt")
obb_discontinuity=YOLO("best (14).pt")
safety=YOLO("yolov8n.pt")

# -----------------------------------------------------------------------------
# 2. MCP Tools Definition (Exposed to System 2 / ZBook)
# -----------------------------------------------------------------------------

CAM_TO_ROBOT_T = np.array([
[0.7328061018, 0.6121545059, -0.2970893437, 0.7217746900],
[0.6799624012, -0.6424940804, 0.3533447178, -0.4958178639],
[-0.0349166256, -0.4652557478, -0.8865538354, 0.8232286668],
[0.0000000000, 0.0000000000, 0.0000000000, 1.0000000000],
], dtype=np.float64)

@mcp.tool()
def get_camera_snapshot() -> str:
    """
    Capture a current RGB frame from the D435i camera.
    Returns the image as a Base64 encoded string for the VLM (System 2) to analyze.
    """
    global current_rgb_frame
    if current_rgb_frame is None:
        return "Error: Camera frame not ready."
    
    # Encode frame to JPEG, then to Base64
    _, buffer = cv2.imencode('.jpg', current_rgb_frame)
    base64_str = base64.b64encode(buffer).decode('utf-8')
    return f"data:image/jpeg;base64,{base64_str}"


@mcp.tool()
def set_tracking_target(target_name: str) -> str:
    """
    Set the object class for System 1 (YOLO) to track.
    System 2 calls this after reasoning. Example target_name: "bottle"
    """
    global current_target_class
    current_target_class = target_name
    return f"Success: Module B is now tracking '{target_name}' at 30Hz."

def rotationMatrixToEulerAngles(R):
    sy = math.sqrt(R[0, 0] * R[0, 0] + R[1, 0] * R[1, 0])
    singular = sy < 1e-6

    if not singular:
        x = math.atan2(R[2, 1], R[2, 2])
        y = math.atan2(-R[2, 0], sy)
        z = math.atan2(R[1, 0], R[0, 0])
    else:
        x = math.atan2(-R[1, 2], R[1, 1])
        y = math.atan2(-R[2, 0], sy)
        z = 0

    return np.array([x, y, z])


def area(x1, y1, x2, y2, x3, y3):
    return abs((x1*(y2-y3) + x2*(y3-y1) + x3*(y1-y2)) / 2.0)

def isinside(point_x, point_y):
    A=area(250, -370, 250, 0, 585, 0)
    A1=area(point_x, point_y, 250, 0, 585, 0)
    A2=area(250, -370, point_x, point_y, 585, 0)
    A3=area(250, -370, 250, 0, point_x, point_y)
    return A==A1+A2+A3


# -----------------------------------------------------------------------------
# 3. Vision Loop (Runs in a separate background thread)
# -----------------------------------------------------------------------------
def vision_loop():
    zoom=1.0

    global current_rgb_frame, current_target_class, latest_3d_coords, last_click, spatial_coords

    # Configure Intel RealSense pipeline
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)

    # Start streaming
    profile = pipeline.start(config)

    # Align depth stream to color stream (Crucial for 3D mapping)
    align_to = rs.stream.color
    align = rs.align(align_to)

    # Get camera intrinsics (Needed for pixel to 3D conversion)
    depth_sensor = profile.get_device().first_depth_sensor()
    camera=profile.get_device()
    advanced_mode = rs.rs400_advanced_mode(camera)
    depth_table=advanced_mode.get_depth_table()
    depth_table.disparityShift=20
    depth_scale = depth_sensor.get_depth_scale()
    intrinsics = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()

    print("[Vision Thread] D435i Camera Started. YOLO Inference Running...")

    try:
        while True:
            frames = pipeline.wait_for_frames()
            aligned_frames = align.process(frames)
            
            depth_frame = aligned_frames.get_depth_frame()
            color_frame = aligned_frames.get_color_frame()
            
            if not depth_frame or not color_frame:
                continue

            # Convert images to numpy arrays
            color_image = np.asanyarray(color_frame.get_data())

            current_rgb_frame = color_image.copy() # Update global state for MCP snapshot

            # Run YOLO inference
            results = model(color_image, verbose=False,agnostic_nms=True,iou=0.40,conf=0.60)
            pipe_detection = segment(color_image, verbose=False,agnostic_nms=True,iou=0.40,conf=0.60)
            solve_discontinuity=obb_discontinuity(color_image, verbose=False,agnostic_nms=True,iou=0.40,conf=0.60)
            hands=safety(color_image, verbose=False,agnostic_nms=True,iou=0.40,conf=0.60)
            current_boxes=[]
            coordinates={}
            #for hand in hands:
            #    boxes=hand.boxes
            #    for box in boxes:
            #        cls_id = int(box.cls[0])
            #        cls_name = safety.names[cls_id]
                    
            #        if cls_name.lower() == "person":
            #            return
                                                                                     
            for pipe in pipe_detection:
                if pipe.masks is not None:
                    masks = pipe.masks.data.cpu().numpy()
                    class_ids = pipe.boxes.cls.cpu().numpy().astype(int)
                    confidences = pipe.boxes.conf.cpu().numpy()

                    for mask, class_id, conf in zip(masks, class_ids, confidences):
                            cls_name = segment.names[class_id]

                            mask_binary = cv2.resize(mask,(color_image.shape[1], color_image.shape[0]))
                            mask_binary = ((mask_binary > 0.5) * 255).astype(np.uint8)
                            colored_mask = color_image.copy()
                            colored_mask[mask_binary > 0] = [215,215,218]
                            color_image = cv2.addWeighted(color_image,0.7,colored_mask,0.3,0)
                            contours, _ = cv2.findContours(mask_binary,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
                            cv2.drawContours(color_image,contours,-1,(0,255,0),2)
                            moment=cv2.moments(contours[0])

                            if moment["m00"] != 0:
                               xcoordinate=int(moment["m10"] / moment["m00"])
                               ycoordinate=int(moment["m01"] / moment["m00"])

                            coordinates[cls_name.lower()]=(xcoordinate,ycoordinate)
                
                            if len(contours) > 0:
                               largest_contour = max(contours, key=cv2.contourArea)
                               x, y, w, h = cv2.boundingRect(largest_contour)
                               cv2.putText(color_image,f"{cls_name} {conf:.2f}",(x, y - 10),cv2.FONT_HERSHEY_SIMPLEX,0.5,(0,255,0),2)

            for result in results:
                if result.obb is None:
                        for discontinuity in solve_discontinuity:
                            boxes = discontinuity.boxes
                            for box in boxes:
                                cls_id = int(box.cls[0])
                                cls_name = obb_discontinuity.names[cls_id]
                                confidence = float(box.conf[0])
                            
                                x1, y1, x2, y2 = map(int, box.xyxy[0])

                                center_x = int((x1 + x2) / 2)
                                center_y = int((y1 + y2) / 2)

                                distance = depth_frame.get_distance(center_x, center_y)
                                median_distance=np.median(distance)
                                if  median_distance > 0:
                                    spatial_coords = rs.rs2_deproject_pixel_to_point(intrinsics, [center_x, center_y], median_distance)
                                    latest_3d_coords["x"] = spatial_coords[0]
                                    latest_3d_coords["y"] = spatial_coords[1]
                                    latest_3d_coords["z"] = spatial_coords[2]


                                    robot = CAM_TO_ROBOT_T @ np.array([spatial_coords[0], spatial_coords[1], spatial_coords[2], 1.0])
                                
                                    cv2.rectangle(color_image, (x1, y1), (x2, y2), (0, 0, 255), 2) # Red box for fallback
                                    cv2.putText(color_image, f"Fallback: {cls_name} {confidence:.2f}", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
                                    cv2.putText(color_image, f"Robot: X:{robot[0]*1000:.1f} Y:{robot[1]*1000:.1f} Z:{robot[2]*1000:.1f}mm", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                    
                else:          
                    for obb in result.obb:
                        cls_id = int(obb.cls[0])
                        cls_name = model.names[cls_id]
                        confidence=float(obb.conf[0])

                    # Draw bounding box
                        (x1, y1), (x2, y2), (x3, y3), (x4, y4) = obb.xyxyxyxy[0].cpu().numpy().astype(int)
                        current_boxes.append((x1,y1,x2,y2,x3,y3,x4,y4,cls_name, confidence))
                        

                    # Draw bounding box
                        cv2.polylines(color_image, [np.array([(x1, y1), (x2, y2), (x3, y3), (x4, y4)], dtype=np.int32)], True, (0, 255, 0), 2)
                        cv2.putText(color_image, f"{cls_name} {confidence:.2f}", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
   
                        rotation_from_matrix=CAM_TO_ROBOT_T[:3,:3]
                    
                    # Calculate center pixel of the bounding box using Depth-Based Top Face Extraction
                        def get_top_face_center(depth_frame, xmin, ymin, xmax, ymax):
                            min_depth = float('inf')
                            closest_pixels = []
                            margin_x = int((xmax - xmin) * 0.1)
                            margin_y = int((ymax - ymin) * 0.1)
                            
                            for y in range(ymin + margin_y, ymax - margin_y):
                                for x in range(xmin + margin_x, xmax - margin_x):
                                    d = depth_frame.get_distance(x, y)
                                    if 0.1 < d < 2.0:
                                        if d < min_depth - 0.01:
                                            min_depth = d
                                            closest_pixels = [(x, y)]
                                        elif abs(d - min_depth) <= 0.01:
                                            closest_pixels.append((x, y))
                            
                            if not closest_pixels:
                                return (xmin + xmax) // 2, (ymin + ymax) // 2
                                
                            avg_x = int(sum(p[0] for p in closest_pixels) / len(closest_pixels))
                            avg_y = int(sum(p[1] for p in closest_pixels) / len(closest_pixels))
                            return avg_x, avg_y
                        
                        center_x, center_y = get_top_face_center(depth_frame, min(x1, x2, x3, x4), min(y1, y2, y3, y4), max(x1, x2, x3, x4), max(y1, y2, y3, y4))
                        angle=float(obb.xywhr[0][4])

                        rotation_from_camera=np.array([[math.cos(angle), -math.sin(angle), 0], 
                                                   [math.sin(angle), math.cos(angle), 0], 
                                                   [0, 0, 1]])
                        
                        
                    # Get distance in meters from the depth frame
                        distance = depth_frame.get_distance(center_x, center_y)
                        median_distance1=np.median(distance)

                    # Deproject pixel to 3D spatial coordinates [X, Y, Z] relative to camera
                        spatial_coords = rs.rs2_deproject_pixel_to_point(intrinsics, [center_x, center_y], median_distance1)
                       
                        # Update global 3D coordinates
                        latest_3d_coords["x"] = spatial_coords[0]
                        latest_3d_coords["y"] = spatial_coords[1]
                        latest_3d_coords["z"] = spatial_coords[2]

                        median_z=np.median(spatial_coords[2])

                        robot=CAM_TO_ROBOT_T @ np.array([spatial_coords[0], spatial_coords[1], median_z, 1.0])
                  
                        if cls_name.lower() in coordinates:
                           center_x, center_y = coordinates[cls_name.lower()]

                        if isinside(robot[0]*1000, robot[1]*1000):
                            height,width,_ = color_image.shape
                            xmin = min(x1, x2, x3, x4)
                            xmax = max(x1, x2, x3, x4)
                            ymin = min(y1, y2, y3, y4)
                            ymax = max(y1, y2, y3, y4)

                            xmin=max(0, xmin)
                            xmax=min(width, xmax)
                            ymin=max(0, ymin)
                            ymax=min(height, ymax)

                            # Prevent empty crop
                            if xmax > xmin and ymax > ymin:
                                cropped=color_image[ymin:ymax, xmin:xmax]

                                # Map the true center to the zoomed-in image dimensions
                                zoomed_x = int((center_x - xmin) * (width / (xmax - xmin)))
                                zoomed_y = int((center_y - ymin) * (height / (ymax - ymin)))

                                cropped_color_image = cv2.resize(cropped, (width, height))
                                cv2.circle(cropped_color_image, (zoomed_x, zoomed_y), 5, (0, 0, 255), -1)
                                color_image=cropped_color_image.copy()
                            else:
                                cv2.circle(color_image, (center_x, center_y), 2, (255,0,0), -1)
                            
                            # Break after zooming once to prevent recursive cropping bugs on multiple objects
                            break

                        else:
                            cv2.circle(color_image, (center_x, center_y), 2, (255,0,0), -1)

                        roll,pitch,yaw=rotationMatrixToEulerAngles(rotation_from_matrix @ rotation_from_camera)
                        cv2.putText(color_image, f"Robot Frame: X:{robot[0]*1000:.4f} Y:{robot[1]*1000:.4f} Z:{robot[2]*1000:.4f}m R:{roll/math.pi*180:.2f} P:{pitch/math.pi*180:.2f} Y:{yaw/math.pi*180:.2f} {cls_name}", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                        print(f"{cls_name} X:{robot[0]*1000:.4f} Y:{robot[1]*1000:.4f} Z:{robot[2]*1000:.4f}m R:{roll/math.pi*180:.2f} P:{pitch/math.pi*180:.2f} Y:{yaw/math.pi*180:.2f}")

            # Show the live feed (for debugging on i5 laptop)
            cv2.imshow("Module B: System 1 Vision Reflex", color_image)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break

            if key == ord('z'):
                zoom+=0.01

            if key == ord('u'):
                zoom-=0.01

    finally:
        pipeline.stop()
        cv2.destroyAllWindows()

# -----------------------------------------------------------------------------
# 4. Main Execution
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Start the vision processing in a background thread
    vision_thread = threading.Thread(target=vision_loop, daemon=True)
    vision_thread.start()

    # Give the camera 2 seconds to warm up
    time.sleep(2)
    print("[MCP Server] Starting FastMCP Server on Main Thread...")
    
    # Run the MCP server (This blocks the main thread, handling API requests)
    mcp.run()