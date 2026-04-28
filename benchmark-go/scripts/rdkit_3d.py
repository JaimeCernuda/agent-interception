# pyright: reportAttributeAccessIssue=false
"""Standalone RDKit subprocess used by the Go ChemCrow agent.

Reads one JSON object from stdin: {"smiles": "...", "op": "smiles_to_3d"|"descriptors"}.
Writes one JSON object to stdout with the result.

Two ops live in one script so the Go side can reuse the same Python startup
across both RDKit-backed tools (saves ~70-100 ms/call on warm Python). The op
is mandatory; pass {"smiles": "...", "op": "smiles_to_3d"} for embed+MMFF94 or
{"smiles": "...", "op": "descriptors"} for descriptor calculation.
"""
from __future__ import annotations

import json
import sys


def smiles_to_3d(smiles: str) -> dict:
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return {"smiles": smiles, "ok": False, "error": "invalid_smiles"}
    mol = Chem.AddHs(mol)
    embed_attempts = 1
    embed_id = AllChem.EmbedMolecule(mol, randomSeed=42)
    if embed_id == -1:
        embed_attempts = 2
        embed_id = AllChem.EmbedMolecule(mol, useRandomCoords=True, randomSeed=42)
    if embed_id == -1:
        return {
            "smiles": smiles,
            "ok": False,
            "error": "embed_failed",
            "embed_attempts": embed_attempts,
        }
    opt_status = AllChem.MMFFOptimizeMolecule(mol, maxIters=200)
    energy: float | None = None
    try:
        props = AllChem.MMFFGetMoleculeProperties(mol)
        ff = AllChem.MMFFGetMoleculeForceField(mol, props)
        if ff is not None:
            energy = float(ff.CalcEnergy())
    except Exception:
        energy = None
    num_atoms = mol.GetNumAtoms()
    num_heavy_atoms = mol.GetNumHeavyAtoms()
    return {
        "smiles": smiles,
        "ok": True,
        "num_atoms": num_atoms,
        "num_heavy_atoms": num_heavy_atoms,
        "energy": energy,
        "embed_attempts": embed_attempts,
        "optimization_status": int(opt_status),
        "optimization_iterations": 200 if opt_status == 1 else 0,
    }


def descriptors(smiles: str) -> dict:
    from rdkit import Chem
    from rdkit.Chem import Crippen, Descriptors, Lipinski

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return {"smiles": smiles, "ok": False, "error": "invalid_smiles"}
    return {
        "smiles": smiles,
        "ok": True,
        "molecular_weight": float(Descriptors.MolWt(mol)),
        "logp": float(Crippen.MolLogP(mol)),
        "tpsa": float(Descriptors.TPSA(mol)),
        "heavy_atom_count": int(Lipinski.HeavyAtomCount(mol)),
        "num_rotatable_bonds": int(Lipinski.NumRotatableBonds(mol)),
    }


def main() -> int:
    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"bad_json: {e}"}))
        return 1
    smiles = req.get("smiles") or ""
    op = req.get("op") or "smiles_to_3d"
    if not smiles:
        print(json.dumps({"ok": False, "error": "missing_smiles"}))
        return 1
    if op == "smiles_to_3d":
        result = smiles_to_3d(smiles)
    elif op == "descriptors":
        result = descriptors(smiles)
    else:
        result = {"ok": False, "error": f"unknown op: {op}"}
    print(json.dumps(result, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
