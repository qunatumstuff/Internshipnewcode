import os
import json
from PIL import Image

DATASET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "TIEFA_Phase1_Dataset")

def verify_dataset():
    print("=" * 60)
    print(" VLA DATASET VERIFICATION PROTOCOL (TIEFA PHASE 1)")
    print("=" * 60)
    
    if not os.path.exists(DATASET_DIR):
        print(f"❌ Error: Dataset directory not found at: {DATASET_DIR}")
        return
        
    episodes = [d for d in os.listdir(DATASET_DIR) if os.path.isdir(os.path.join(DATASET_DIR, d)) and d.startswith("task_")]
    print(f"Found {len(episodes)} dataset episodes (episodes).")
    
    valid_count = 0
    formatted_dataset = []
    
    for ep in sorted(episodes):
        ep_dir = os.path.join(DATASET_DIR, ep)
        print(f"\nChecking episode: {ep}...")
        
        # 1. Verify files exist
        img_path = os.path.join(ep_dir, "image.jpg")
        inst_path = os.path.join(ep_dir, "instruction.txt")
        pose_path = os.path.join(ep_dir, "pose_action.json")
        
        missing = []
        if not os.path.exists(img_path): missing.append("image.jpg")
        if not os.path.exists(inst_path): missing.append("instruction.txt")
        if not os.path.exists(pose_path): missing.append("pose_action.json")
        
        if missing:
            print(f"  ❌ Missing files: {', '.join(missing)}")
            continue
            
        # 2. Verify image
        try:
            with Image.open(img_path) as img:
                img.verify()
            # Note: Verify image is clean
            print("  ✅ image.jpg: Valid uncorrupted image.")
        except Exception as e:
            print(f"  ❌ image.jpg: Corrupt or invalid image ({e})")
            continue
            
        # 3. Verify instruction
        try:
            with open(inst_path, "r", encoding="utf-8") as f:
                instruction = f.read().strip()
            if not instruction:
                print("  ❌ instruction.txt: Empty text.")
                continue
            print(f"  ✅ instruction.txt: '{instruction}'")
        except Exception as e:
            print(f"  ❌ instruction.txt: Failed to read ({e})")
            continue
            
        # 4. Verify pose JSON
        try:
            with open(pose_path, "r", encoding="utf-8") as f:
                pose_data = json.load(f)
            
            sid = pose_data.get("session_id")
            pose = pose_data.get("pose")
            gripper = pose_data.get("gripper")
            
            if sid != ep:
                print(f"  ❌ pose_action.json: session_id mismatch (Folder={ep}, JSON={sid})")
                continue
                
            if not isinstance(pose, list) or len(pose) != 6:
                print("  ❌ pose_action.json: 'pose' must be a list of 6 values [X, Y, Z, Roll, Pitch, Yaw].")
                continue
                
            print(f"  ✅ pose_action.json: Pose {pose}, Gripper {gripper}")
            
            # Add to SFT conversations list
            formatted_dataset.append({
                "id": ep,
                "image": f"{ep}/image.jpg",
                "conversations": [
                    {
                        "from": "user",
                        "value": f"Picture 1: <img>image.jpg</img>\nTask: {instruction}\nOutput the 6D coordinate pose (X, Y, Z in mm, and Roll, Pitch, Yaw in degrees) for LARA arm to execute."
                    },
                    {
                        "from": "assistant",
                        "value": f"Pose: {json.dumps(pose)}, Gripper: {gripper}"
                    }
                ]
            })
            
            valid_count += 1
            
        except Exception as e:
            print(f"  ❌ pose_action.json: Failed to parse or read ({e})")
            continue
            
    print("\n" + "=" * 60)
    print(f" SUMMARY: {valid_count} / {len(episodes)} episodes are valid.")
    print("=" * 60)
    
    if valid_count > 0:
        out_path = os.path.join(DATASET_DIR, "vla_sft_dataset.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(formatted_dataset, f, indent=2)
        print(f"✅ Generated training annotation dataset at: {out_path}")
        print("\nReady for Parameter-Efficient Fine-Tuning (LoRA)!")
        
        print_lora_training_instructions()

def print_lora_training_instructions():
    print("""
======================================================================
 PEFT / LoRA (LARA-Adapter) Model Fine-Tuning Boilerplate
======================================================================

Below is the standard python script skeleton to freeze the 4B/2B backbone 
and train the lightweight side-network adapter on the RTX GPU:

----------------------------------------------------------------------
import torch
from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
from peft import LoraConfig, get_peft_model, TaskType

# 1. Load frozen backbone
model_id = "Qwen/Qwen2-VL-2B-Instruct" # Or Qwen2-VL-7B-Instruct
model = Qwen2VLForConditionalGeneration.from_pretrained(
    model_id, 
    torch_dtype=torch.float16, 
    device_map="auto"
)

# Freeze all backbone parameters
for param in model.parameters():
    param.requires_grad = False

# 2. Inject LoRA adapters to attention layers
lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
    lora_dropout=0.05,
    bias="none",
    task_type=TaskType.CAUSAL_LM
)

model = get_peft_model(model, lora_config)
model.print_trainable_parameters()
# Trainable params: ~1-2% of backbone parameters (e.g. ~50MB side-network)

# 3. Setup Dataset Loader using 'vla_sft_dataset.json'
# Implement training loop / Trainer to regress coordinates.
# Optimise with AdamW and learn Cartesian actions directly.
----------------------------------------------------------------------
""")

if __name__ == "__main__":
    verify_dataset()
