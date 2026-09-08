import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        # Exercise the actual bundled Tk runtime on the release runner, without
        # leaving an interactive window open or relying on a local Python install.
        from pathlib import Path
        import traceback
        try:
            import tkinter as tk
            from sysdoc.gui import SysdocWindow
            from sysdoc.scanners.storage import StorageScanner
            from sysdoc.core.orchestrator import Orchestrator
            root = tk.Tk()
            root.withdraw()
            window = SysdocWindow(root)
            window._show_results(Orchestrator([StorageScanner()]).run_all())
            root.update()
            assert window.update_button.cget("text") == "Check for updates"
            assert "Storage scan" in window.output.get("1.0", "end")
            root.destroy()
        except Exception:
            Path(sys.argv[2]).write_text(traceback.format_exc(), encoding="utf-8")
            sys.exit(1)
        Path(sys.argv[2]).write_text("Desktop bundle smoke test passed", encoding="utf-8")
    else:
        from sysdoc.gui import main
        main()
