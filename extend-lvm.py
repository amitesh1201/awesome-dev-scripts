import subprocess
import os
import sys
import re
from datetime import datetime

# LVM Partition Expansion Script (Python)
# This script helps to find a new hard drive, initialize it as a Physical Volume (PV),
# extend an existing Volume Group (VG) with this new PV, and then expand a
# Logical Volume (LV) and its filesystem.

# !!! WARNING !!!
# This script performs critical system modifications.
# IMPROPER USE CAN LEAD TO IRREVERSIBLE DATA LOSS.
# ENSURE YOU HAVE A COMPLETE BACKUP OF YOUR SYSTEM BEFORE PROCEEDING.

# --- Configuration ---
LOG_FILE = f"/var/log/lvm_expansion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
# --- End Configuration ---

def setup_logging():
    """Sets up logging to a file and duplicates output to console."""
    sys.stdout = Logger(LOG_FILE)
    sys.stderr = sys.stdout # Redirect stderr to the same logger

class Logger:
    """Custom logger to write to both file and stdout/stderr."""
    def __init__(self, filename):
        self.terminal = sys.__stdout__
        self.file = open(filename, "a")

    def write(self, message):
        self.terminal.write(message)
        self.file.write(message)

    def flush(self):
        self.terminal.flush()
        self.file.flush()

def check_command(cmd):
    """Checks if a command exists in the system's PATH."""
    if not subprocess.run(["command", "-v", cmd], capture_output=True, text=True).returncode == 0:
        print(f"Error: Required command '{cmd}' not found. Please install it.")
        sys.exit(1)

def run_command(cmd_list, error_msg="", confirm_msg="", allow_failure=False):
    """
    Runs a shell command and handles its output and errors.
    If confirm_msg is provided, asks for user confirmation.
    """
    if confirm_msg:
        print(confirm_msg)
        input("Press Enter to continue, or Ctrl+C to abort.")

    try:
        result = subprocess.run(cmd_list, capture_output=True, text=True, check=False)
        if result.returncode != 0 and not allow_failure:
            print(f"Error: {error_msg}")
            print(f"Command: {' '.join(cmd_list)}")
            print(f"Stdout:\n{result.stdout}")
            print(f"Stderr:\n{result.stderr}")
            sys.exit(1)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except FileNotFoundError:
        print(f"Error: Command '{cmd_list[0]}' not found. Is it installed and in your PATH?")
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)

def confirm_danger(message):
    """Asks for explicit 'YES' confirmation for dangerous operations."""
    print(f"\n!!! DANGER !!!\n{message}")
    confirmation = input("Type 'YES' (in capitals) to confirm: ").strip()
    if confirmation != "YES":
        print("Confirmation failed. Aborting to prevent data loss.")
        sys.exit(1)

def get_block_devices():
    """Lists potential new hard drives, filtering for unmounted disks."""
    print("Listing potential new hard drives. Look for entries where TYPE is 'disk' and MOUNTPOINT is empty.")
    print("If FSTYPE is also empty, it will be marked as '[UNPARTITIONED]'.")
    print("-" * 100)
    
    # lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE,KNAME --noheadings
    # NAME        SIZE FSTYPE MOUNTPOINT TYPE  KNAME
    # sda         20G              disk  sda
    # ├─sda1      500M ext4   /boot    part  sda1
    # └─sda2     19.5G lvm           part  sda2
    #   ├─vg-lv_root 15G ext4   /        lvm   vg-lv_root
    #   └─vg-lv_swap 4.5G swap   [SWAP]   lvm   vg-lv_swap
    # sdb         10G              disk  sdb
    
    output, _, _ = run_command(["lsblk", "-o", "NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE,KNAME", "--noheadings"], 
                                "Failed to list block devices.", allow_failure=True)
    
    potential_drives = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 6: # Ensure enough columns
            continue

        name = parts[0]
        size = parts[1]
        fstype = parts[2] if parts[2] != '' else "[UNPARTITIONED]"
        mountpoint = parts[3]
        dev_type = parts[4]
        kname = parts[5]

        # Filter for 'disk' type and empty mountpoint
        if dev_type == "disk" and mountpoint == "":
            potential_drives.append(f"{name:<10} {size:<10} {fstype:<15} {mountpoint:<10} {dev_type:<10} {kname}")
    
    if potential_drives:
        for drive in potential_drives:
            print(drive)
    else:
        print("No potential unmounted disk drives found.")

    print("-" * 100)
    return potential_drives

def main():
    setup_logging()
    print("--- LVM Partition Expansion Script ---")
    print(f"Log file: {LOG_FILE}")
    print("")

    # Ensure script is run as root
    if os.geteuid() != 0:
        print("Error: This script must be run as root. Please use 'sudo'.")
        sys.exit(1)

    # Check for necessary commands
    required_commands = ["lsblk", "pvcreate", "vgextend", "lvextend",
                         "resize2fs", "xfs_growfs", "pvs", "vgs", "lvs",
                         "df", "findmnt", "parted", "pvscan"]
    for cmd in required_commands:
        check_command(cmd)

    print("--- Step 1: Identify the new hard drive ---")
    get_block_devices()

    new_drive_kname = input("Enter the KNAME of the new hard drive (e.g., sdb, sdc): ").strip()
    if not new_drive_kname:
        print("Error: No new drive name entered. Exiting.")
        sys.exit(1)

    new_drive_path = f"/dev/{new_drive_kname}"

    # Basic check if the device path exists
    if not os.path.exists(new_drive_path):
        print(f"Error: Device '{new_drive_path}' does not exist. Please check the KNAME.")
        sys.exit(1)

    confirm_danger(f"You have selected '{new_drive_path}'.\n"
                   f"DOUBLE-CHECK THIS IS THE CORRECT, EMPTY DRIVE YOU INTEND TO USE.\n"
                   f"ALL DATA ON THIS DRIVE WILL BE LOST.")

    # Check if the disk already has partitions or LVM signatures
    print(f"Checking for existing partitions or LVM signatures on {new_drive_path}...")
    parted_check, _, parted_rc = run_command(["parted", "-s", new_drive_path, "print"], allow_failure=True)
    if parted_rc == 0 and "Error" not in parted_check and "unrecognized disk label" not in parted_check: # parted prints something for a raw disk, but errors for uninitialized
        print(f"Warning: '{new_drive_path}' appears to have existing partition table.")
        overwrite_confirm = input("Are you absolutely sure you want to proceed and overwrite it? Type 'yes' to continue: ").strip()
        if overwrite_confirm != "yes":
            print("Aborting. Please ensure the drive is truly empty or back up its data.")
            sys.exit(1)
    
    # pvscan check for LVM signatures
    pvscan_check, _, pvscan_rc = run_command(["pvscan", new_drive_path], allow_failure=True)
    if "PV Name" in pvscan_check or "is in use" in pvscan_check or pvscan_rc == 0:
        print(f"Warning: '{new_drive_path}' already appears to be an LVM Physical Volume or in use.")
        reuse_pv_confirm = input("Do you want to proceed assuming it's okay to reuse this PV? Type 'yes' to continue: ").strip()
        if reuse_pv_confirm != "yes":
            print("Aborting. Please ensure the drive is truly new or prepare it accordingly.")
            sys.exit(1)

    print("\n--- Step 2: Create a Physical Volume (PV) ---")
    print(f"Initializing '{new_drive_path}' as an LVM Physical Volume.")
    print(f"This will wipe any existing data and partition tables on '{new_drive_path}'.")
    run_command(["pvcreate", new_drive_path], "pvcreate failed.")
    print(f"Physical Volume '{new_drive_path}' created successfully.")
    run_command(["pvs"])

    print("\n--- Step 3: Extend an existing Volume Group (VG) ---")
    print("Listing existing Volume Groups:")
    run_command(["vgs"])

    target_vg_name = input("Enter the name of the Volume Group (VG) to expand (e.g., vg_system): ").strip()
    if not target_vg_name:
        print("Error: No VG name entered. Exiting.")
        sys.exit(1)

    _, _, vg_exists_rc = run_command(["vgs", target_vg_name], allow_failure=True)
    if vg_exists_rc != 0:
        print(f"Error: Volume Group '{target_vg_name}' does not exist. Exiting.")
        sys.exit(1)

    print(f"Adding '{new_drive_path}' to Volume Group '{target_vg_name}'.")
    run_command(["vgextend", target_vg_name, new_drive_path], "vgextend failed.")
    print(f"Volume Group '{target_vg_name}' extended successfully.")
    run_command(["vgs"])

    print("\n--- Step 4: Expand a Logical Volume (LV) ---")
    print(f"Listing Logical Volumes in Volume Group '{target_vg_name}':")
    run_command(["lvs", target_vg_name])

    target_lv_name = input("Enter the name of the Logical Volume (LV) to expand (e.g., lv_root): ").strip()
    if not target_lv_name:
        print("Error: No LV name entered. Exiting.")
        sys.exit(1)

    target_lv_path = f"/dev/{target_vg_name}/{target_lv_name}"

    _, _, lv_exists_rc = run_command(["lvs", target_lv_path], allow_failure=True)
    if lv_exists_rc != 0:
        print(f"Error: Logical Volume '{target_lv_path}' does not exist in VG '{target_vg_name}'. Exiting.")
        sys.exit(1)

    print(f"Logical Volume '{target_lv_path}' selected for expansion.")

    # Calculate max available space in the VG for the LV
    vg_free_output, _, _ = run_command(["vgs", "--noheadings", "-o", "vg_free", "--unit", "m", target_vg_name])
    free_vg_space_mib = int(float(vg_free_output.split()[0].replace(',', ''))) if vg_free_output else 0
    print(f"Approximately {free_vg_space_mib}MiB free in Volume Group '{target_vg_name}'.")

    expand_size = input(f"How much space (e.g., 10G, 500M) do you want to add to '{target_lv_path}'? (Or type 'MAX' to use all available space): ").strip().upper()

    lve_command = ["lvextend"]
    if expand_size == "MAX":
        print(f"Expanding Logical Volume '{target_lv_path}' to use all available space.")
        lve_command.extend(["-l", "+100%FREE"])
    else:
        # Basic validation
        if not re.fullmatch(r"^[0-9]+[GgMmTt]$", expand_size):
            print("Error: Invalid size format. Please use e.g. 10G or 500M. Exiting.")
            sys.exit(1)
        print(f"Adding {expand_size} to Logical Volume '{target_lv_path}'.")
        lve_command.extend(["-L", f"+{expand_size}"])

    run_command(lve_command + [target_lv_path], "lvextend failed.")
    print(f"Logical Volume '{target_lv_path}' expanded successfully.")
    run_command(["lvs", target_lv_path])

    print("\n--- Step 5: Resize the Filesystem ---")
    # Get the mount point of the LV
    mount_point_output, _, _ = run_command(["findmnt", "-no", "TARGET", target_lv_path], allow_failure=True)
    mount_point = mount_point_output.strip()

    if not mount_point:
        print(f"Warning: Could not find mount point for '{target_lv_path}'.")
        print("You will need to manually resize the filesystem. Use 'df -hT' to find the mount point.")
        print(f"Then use 'resize2fs {target_lv_path}' (for extX) or 'xfs_growfs /mount/point' (for XFS).")
        print("Script completed with a warning. Please resize filesystem manually.")
        sys.exit(0)

    print(f"Logical Volume '{target_lv_path}' is mounted at '{mount_point}'.")
    fs_type_output, _, _ = run_command(["df", "-T", mount_point], "Failed to get filesystem type.")
    fs_type = fs_type_output.splitlines()[1].split()[1]

    if fs_type in ["ext2", "ext3", "ext4"]:
        print(f"Filesystem type is {fs_type}. Resizing with resize2fs...")
        run_command(["resize2fs", target_lv_path], "resize2fs failed. Please check the filesystem for errors.")
        print("Filesystem resized successfully!")
    elif fs_type == "xfs":
        print("Filesystem type is XFS. Resizing with xfs_growfs...")
        # xfs_growfs works on the mount point
        run_command(["xfs_growfs", mount_point], "xfs_growfs failed. Please check the filesystem for errors.")
        print("Filesystem resized successfully!")
    else:
        print(f"Error: Unsupported filesystem type '{fs_type}' on '{mount_point}'.")
        print(f"You will need to manually resize the filesystem. Please consult documentation for '{fs_type}'.")
        print("Script completed with an error due to unsupported filesystem.")
        sys.exit(1)

    print("\n--- LVM Expansion Complete! ---")
    print(f"The Logical Volume '{target_lv_path}' and its filesystem have been expanded.")
    print("Current disk usage:")
    run_command(["df", "-hT", mount_point])
    print("Please verify everything is as expected.")

if __name__ == "__main__":
    main()

