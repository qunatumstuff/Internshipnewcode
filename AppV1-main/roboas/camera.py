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
import inference
import vision

# -----------------------------------------------------------------------------
# 1. Global State Variables (Shared between Vision Thread and MCP Server)
# -----------------------------------------------------------------------------
current_rgb_frame = None
current_target_class = None  # e.g., "cup", "bottle", "apple"
latest_3d_coords = {"x": 0.0, "y": 0.0, "z": 0.0}
current_depth_frame = None
camera_intrinsics = None

# Initialize F                                                                                                                                                          1`astMCP Server
mcp = FastMCP("TIEFA_Module_B_Vision")

model=YOLO("best16.pt")
segment=YOLO("best13.pt")

# -----------------------------------------------------------------------------
# 2. MCP Tools Definition (Exposed to System 2 / ZBook)
# -----------------------------------------------------------------------------

tracked=False

@mcp.tool()
def get_camera_snapshot() -> str:
    """
    Capture a current RGB frame from the D435i camera.
    Returns the image as a Base64 encoded string for the VLM (System 2) to analyze.
    If a question is provided, asks the question to Qwen and returns the text response.
    """
    global current_rgb_frame
    if current_rgb_frame is None:
        return "Error: Camera frame not ready."
    
    # Encode frame to JPEG, then to Base64
    _, buffer = cv2.imencode('.jpg', current_rgb_frame)
    base64_str = base64.b64encode(buffer).decode('utf-8')
    print("Took snapshot")
    
    return f"data:image/jpeg;base64,{base64_str}"


@mcp.tool()
def set_tracking_target(target_name: str) -> str:
    """
    Set the object class for System 1 (YOLO) to track.
    System 2 calls this after reasoning. Example target_name: "bottle"
    """
    global current_target_class
    current_target_class = target_name
    print(current_target_class)

    return current_target_class

# -----------------------------------------------------------------------------
# 3. Vision Loop (Runs in a separate background thread)
# -----------------------------------------------------------------------------

def rotationMatrixToEulerAngles(R):
    sy = math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
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



def vision_loop():
    global current_rgb_frame, current_target_class, latest_3d_coords, last_click, spatial_coords, current_depth_frame, camera_intrinsics

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
    camera_intrinsics = intrinsics

    print("[Vision Thread] D435i Camera Started. YOLO Inference Running...")

    clicked=""

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
            current_depth_frame = depth_frame

            if current_target_class is None:
                print("No target")

            elif current_target_class=="sponge":
                sponge_detection = segment(color_image, verbose=False,agnostic_nms=True,iou=0.35,conf=0.35)

                print("Object is ",current_target_class)
                for sponge in sponge_detection:
                    if sponge.masks is not None:
                        masks = sponge.masks.data.cpu().numpy()
                        class_ids = sponge.boxes.cls.cpu().numpy().astype(int)
                        confidences = sponge.boxes.conf.cpu().numpy()

                        for mask, class_id, conf in zip(masks, class_ids, confidences):
                                cls_name = segment.names[class_id]

                                if cls_name==current_target_class.lower():
                                    mask_binary = cv2.resize(mask,(color_image.shape[1], color_image.shape[0]))
                                    mask_binary = ((mask_binary > 0.5) * 255).astype(np.uint8)
                                    colored_mask = color_image.copy()
                                    colored_mask[mask_binary > 0] = [215,215,218]
                                    color_image = cv2.addWeighted(color_image,0.7,colored_mask,0.3,0)
                                    contours, _ = cv2.findContours(mask_binary,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
                                    cv2.drawContours(color_image,contours,-1,(0,255,0),2)
                            
                                    if len(contours) > 0:
                                        largest_contour = max(contours, key=cv2.contourArea)
                                        x, y, w, h = cv2.boundingRect(largest_contour)
                                        cv2.putText(color_image,f"{cls_name} {conf:.2f}",(x, y - 10),cv2.FONT_HERSHEY_SIMPLEX,0.5,(0,255,0),2)
            else:  
                    results = model(color_image, verbose=False,agnostic_nms=True,iou=0.35,conf=0.35)
                    current_boxes=[]
                    print("object is not pipe", current_target_class)                
                    for result in results:
                        if result.obb is None:
                            continue

                        for obb in result.obb:
                            cls_id = int(obb.cls[0])
                            cls_name = model.names[cls_id]
                            confidence=float(obb.conf[0])

                            if cls_name == current_target_class.lower():
                               (x1, y1), (x2, y2), (x3, y3), (x4, y4) = obb.xyxyxyxy[0].cpu().numpy().astype(int)
                               current_boxes.append((x1,y1,x2,y2,x3,y3,x4,y4,cls_name, confidence))


                    # Draw bounding box
                               cv2.polylines(color_image, [np.array([(x1, y1), (x2, y2), (x3, y3), (x4, y4)], dtype=np.int32)], True, (0, 255, 0), 2)
                               cv2.putText(color_image, f"{cls_name} {confidence:.2f}", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)


                               CAM_TO_ROBOT_T = np.array([
                             [0.7337634310,  0.6126652048, -0.2936538341,  0.7173839756],
                             [0.6785283256, -0.6388791698, 0.3625365054, -0.4903506740],
                             [0.0345041846, -0.4652684744, -0.8844968672, 0.7880605490],
                             [0.0, 0.0, 0.0, 1.0]
                            ], dtype=np.float64)
                    
                               rotation_from_matrix=CAM_TO_ROBOT_T[:3,:3]
                    
                    # Calculate center pixel of the bounding box
                               center_x = int(obb.xywhr[0][0])
                               center_y = int(obb.xywhr[0][1])
                               angle=float(obb.xywhr[0][4])

                               rotation_from_camera=np.array([[math.cos(angle), -math.sin(angle), 0], 
                                                   [math.sin(angle), math.cos(angle), 0], 
                                                   [0, 0, 1]])

                    # Get distance in meters from the depth frame
                               distance = depth_frame.get_distance(center_x, center_y)

                    # Deproject pixel to 3D spatial coordinates [X, Y, Z] relative to camera
                               spatial_coords = rs.rs2_deproject_pixel_to_point(intrinsics, [center_x, center_y], distance)

                        # Update global 3D coordinates
                               latest_3d_coords["x"] = spatial_coords[0]
                               latest_3d_coords["y"] = spatial_coords[1]
                               latest_3d_coords["z"] = spatial_coords[2] + 0.02
                    
                               roll,pitch,yaw=rotationMatrixToEulerAngles(rotation_from_matrix @ rotation_from_camera)
                    
                               robot=CAM_TO_ROBOT_T @ np.array([spatial_coords[0], spatial_coords[1], spatial_coords[2], 1.0])
                               cv2.putText(color_image, f"Robot Frame: X:{robot[0]*1000:.4f} Y:{robot[1]*1000:.4f} Z:{robot[2]*1000:.4f}m R:{roll/math.pi*180:.2f} P:{pitch/math.pi*180:.2f} Y:{yaw/math.pi*180:.2f} {cls_name}", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                               print(f"{cls_name} X:{robot[0]*1000:.4f} Y:{robot[1]*1000:.4f} Z:{robot[2]*1000:.4f}m R:{roll/math.pi*180:.2f} P:{pitch/math.pi*180:.2f} Y:{yaw/math.pi*180:.2f}")
                        

            # Show the live feed (for debugging on i5 laptop)
            cv2.imshow("Module B: System 1 Vision Reflex", color_image)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

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