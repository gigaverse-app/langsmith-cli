import subprocess
import sys


def run_cli(args):
    """Bridge function to run the langsmith-cli and return output."""
    cmd = ["langsmith-cli"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        return f"Error: {e.stderr}"


if __name__ == "__main__":
    # If called directly, just pass through
    print(run_cli(sys.argv[1:]))
