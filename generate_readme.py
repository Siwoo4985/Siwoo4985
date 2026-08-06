"""Entry point for updating profile README SVGs via GitHub Actions."""

import sys
import today

if __name__ == "__main__":
    print("Starting GitHub README Profile stats update...")
    try:
        today.main()
        print("Profile stats update completed successfully!")
    except Exception as e:
        print(f"Error during execution: {e}", file=sys.stderr)
        sys.exit(1)
