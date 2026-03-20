from daqhats import hat_list, HatIDs

def check_for_mcc128():
    """
    Scans for connected MCC 128 devices and prints the result.
    """
    try:
        hats = hat_list(filter_by_id=HatIDs.MCC_128)
        if not hats:
            print("No MCC 128 devices found.")
        else:
            print(f"Found {len(hats)} MCC 128 device(s):")
            for hat in hats:
                print(f"  - Address: {hat.address}")
    except Exception as e:
        print(f"An error occurred while scanning for devices: {e}")

if __name__ == "__main__":
    check_for_mcc128()
