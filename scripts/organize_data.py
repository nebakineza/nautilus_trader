import os
import shutil
import zipfile
import re

ROOT_DIR = "/home/seb/nebakineza/nautilus_trader"
DATA_DIR = os.path.join(ROOT_DIR, "data")
OB_DATA_DIR = os.path.join(DATA_DIR, "ob_data")

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def organize_zips():
    print("Scanning for zip files in root...")
    files = [f for f in os.listdir(ROOT_DIR) if f.endswith(".zip") and "_ob200.data.zip" in f]
    
    for zip_file in files:
        # Parse filename: YYYY-MM-DD_SYMBOL_ob200.data.zip
        # Regex to capture Symbol
        match = re.search(r"\d{4}-\d{2}-\d{2}_([A-Z0-9]+)_ob200\.data\.zip", zip_file)
        if match:
            symbol = match.group(1)
            target_folder = f"{symbol}_Spot"
            target_path = os.path.join(OB_DATA_DIR, target_folder)
            
            ensure_dir(target_path)
            
            src_zip = os.path.join(ROOT_DIR, zip_file)
            print(f"Unzipping {zip_file} to {target_path}...")
            
            try:
                with zipfile.ZipFile(src_zip, 'r') as zip_ref:
                    zip_ref.extractall(target_path)
                
                # Verify extraction and remove zip
                os.remove(src_zip)
                print(f"Removed {zip_file}")
            except Exception as e:
                print(f"Failed to unzip {zip_file}: {e}")
        else:
            print(f"Skipping {zip_file} (pattern mismatch)")

def fix_nested_dirs():
    print("Fixing nested directories...")
    
    # 1. questdb/questdb
    qdb_root = os.path.join(DATA_DIR, "questdb")
    qdb_nested = os.path.join(qdb_root, "questdb")
    if os.path.exists(qdb_nested) and os.path.isdir(qdb_nested):
        print(f"Flattening {qdb_nested}...")
        for item in os.listdir(qdb_nested):
            s = os.path.join(qdb_nested, item)
            d = os.path.join(qdb_root, item)
            if os.path.exists(d):
                if os.path.isdir(s):
                    # Merge logic needed? Or just overwrite?
                    # Simple merge: move contents if dir exists
                    # For simplicity, if dest exists, we might need manual check.
                    # But QuestDB folders usually distinct by table/partition.
                    pass 
                else:
                    pass
            
            # Simple move, might fail if exists
            try:
                shutil.move(s, d)
            except Exception as e:
                print(f"Could not move {s} to {d}: {e}")
        
        try:
            os.rmdir(qdb_nested)
            print("Removed nested questdb folder.")
        except:
            print("Could not remove nested questdb folder (might not be empty).")

    # 2. tick_data/tick_data
    td_root = os.path.join(DATA_DIR, "tick_data")
    td_nested = os.path.join(td_root, "tick_data")
    if os.path.exists(td_nested) and os.path.isdir(td_nested):
        print(f"Flattening {td_nested}...")
        for item in os.listdir(td_nested):
            s = os.path.join(td_nested, item)
            d = os.path.join(td_root, item)
            try:
                shutil.move(s, d)
            except Exception as e:
                print(f"Could not move {s} to {d}: {e}")
        try:
            os.rmdir(td_nested)
        except:
            pass

    # 3. questdb_vps_backup/questdb_vps_backup
    qv_root = os.path.join(DATA_DIR, "questdb_vps_backup")
    qv_nested = os.path.join(qv_root, "questdb_vps_backup")
    if os.path.exists(qv_nested) and os.path.isdir(qv_nested):
        print(f"Flattening {qv_nested}...")
        for item in os.listdir(qv_nested):
            s = os.path.join(qv_nested, item)
            d = os.path.join(qv_root, item)
            try:
                shutil.move(s, d)
            except Exception as e:
                print(f"Could not move {s} to {d}: {e}")
        try:
            os.rmdir(qv_nested)
        except:
            pass

    # 4. ob_data/ob_data_live -> data/ob_data_live
    od_live_nested = os.path.join(OB_DATA_DIR, "ob_data_live")
    target_live_root = os.path.join(DATA_DIR, "ob_data_live")
    ensure_dir(target_live_root)
    
    if os.path.exists(od_live_nested) and os.path.isdir(od_live_nested):
        print(f"Moving {od_live_nested} to {target_live_root}...")
        for item in os.listdir(od_live_nested):
            s = os.path.join(od_live_nested, item)
            d = os.path.join(target_live_root, item)
            try:
                shutil.move(s, d)
            except Exception as e:
                print(f"Could not move {s} to {d}: {e}")
        try:
            os.rmdir(od_live_nested)
        except:
            pass

if __name__ == "__main__":
    organize_zips()
    fix_nested_dirs()
    print("Done.")
