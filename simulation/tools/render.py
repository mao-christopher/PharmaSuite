"""Run Unity offline export; preserves real rendering (do not add -nographics)."""
import argparse
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--unity", required=True, type=Path, help="Unity editor executable")
parser.add_argument("--output", required=True, type=Path, help="New recording directory")
parser.add_argument("--preview", action="store_true", help="Action sample frames instead of full recording")
parser.add_argument("--ambiguous", action="store_true", help="Occlude the correct return during the workflow")
parser.add_argument("--rebuild", action="store_true", help="Regenerate the scene first")
parser.add_argument("--camera", choices=["front", "side"], default="front", help="Calibrated camera preset")
args = parser.parse_args()
project = Path(__file__).resolve().parents[1]
output = args.output.resolve()
if output.exists() and any(output.iterdir()):
    parser.error("Output directory must be new or empty")
output.parent.mkdir(parents=True, exist_ok=True)
base = [str(args.unity), "-batchmode", "-projectPath", str(project), "-quit"]
if args.rebuild or not (project / "Assets/Pharma/Generated/Pharmacy.unity").exists():
    subprocess.run(base + ["-executeMethod", "Pharma.Simulation.Editor.PharmacySceneBuilder.Build",
                           "-logFile", str(output.parent / (output.name + "-build.log"))], check=True)
command = base + ["-executeMethod", "Pharma.Simulation.Editor.SimulationExporter.Export",
                  "-pharmaOutput", str(output), "-pharmaCamera", args.camera, "-logFile", str(output.parent / (output.name + "-render.log"))]
if args.preview:
    command.append("-pharmaPreview")
if args.ambiguous:
    command.append("-pharmaAmbiguous")
subprocess.run(command, check=True)
if not args.preview:
    subprocess.run([sys.executable, str(project / "tools/package_recording.py"), str(output)], check=True)
