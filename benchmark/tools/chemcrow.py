# pyright: reportAttributeAccessIssue=false
"""Chemistry tools for the ChemCrow benchmark.

Three tools instrumented with obs spans matching the existing schema:
  - lookup_molecule:    name -> SMILES + MolecularWeight via PubChem REST
  - smiles_to_3d:       SMILES -> 3D conformer + MMFF94 energy via RDKit
  - compute_descriptors: SMILES -> MW, logP, TPSA, heavy-atom count, rotatable bonds

These are plain Python functions; the agent-side adapter (config_chemcrow_py.py)
wraps them in claude-agent-sdk @tool decorators and threads the Observer in via
a contextvar.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from benchmark.obs import Observer, input_hash

_PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
_PUBCHEM_TIMEOUT = 15.0
_CACHE_DIR = Path(__file__).resolve().parents[1] / "cache" / "pubchem"


def lookup_molecule(name: str, obs: Observer) -> dict:
    """Look up a molecule by name on PubChem; return SMILES + molecular weight.

    Cached on disk under benchmark/cache/pubchem/{name}.json. Cache hits skip
    the HTTP call but still emit a tool.lookup_molecule span tagged with
    tool.cache_hit=True.
    """
    with obs.span(
        "tool.lookup_molecule",
        **{
            "tool.name": "lookup_molecule",
            "tool.input_hash": input_hash(name),
            "tool.molecule_name": name,
            "tool.retry_count": 0,
        },
    ) as span:
        cache_path = _CACHE_DIR / f"{_safe_filename(name)}.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                span.set("tool.cache_hit", True)
                span.set("tool.smiles", cached.get("smiles", ""))
                span.set("tool.molecular_weight", float(cached.get("molecular_weight") or 0.0))
                span.set("tool.output_size_bytes", len(json.dumps(cached).encode("utf-8")))
                return cached
            except (OSError, ValueError):
                pass

        span.set("tool.cache_hit", False)
        url = (
            f"{_PUBCHEM_BASE}/compound/name/{_url_quote(name)}"
            f"/property/CanonicalSMILES,MolecularWeight/JSON"
        )
        result, retries, status = _http_get_json(url)
        span.set("tool.retry_count", retries)
        span.set("tool.http_status", status)

        smiles, mw = _extract_pubchem(result)
        payload = {
            "name": name,
            "smiles": smiles,
            "molecular_weight": mw,
        }
        if smiles:
            try:
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(payload, indent=2))
            except OSError:
                pass
        span.set("tool.smiles", smiles)
        span.set("tool.molecular_weight", float(mw or 0.0))
        span.set("tool.output_size_bytes", len(json.dumps(payload).encode("utf-8")))
        return payload


def smiles_to_3d(smiles: str, obs: Observer) -> dict:
    """Generate a 3D conformer for a SMILES string. Pure RDKit, no I/O.

    Returns {smiles, num_atoms, num_heavy_atoms, energy, embed_status,
    optimization_status, coords (list)}. Span attrs include
    rdkit.embed_attempts (1 or 2 if first embed failed) and
    rdkit.optimization_iterations (a coarse proxy: 0 if MMFF returned 0/success,
    -1 if it didn't converge).
    """
    from rdkit import Chem
    from rdkit.Chem import AllChem

    with obs.span(
        "tool.smiles_to_3d",
        **{
            "tool.name": "smiles_to_3d",
            "tool.input_hash": input_hash(smiles),
            "tool.smiles": smiles,
            "tool.retry_count": 0,
        },
    ) as span:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            span.set("tool.error", "invalid_smiles")
            return {
                "smiles": smiles,
                "ok": False,
                "error": "invalid_smiles",
            }
        mol = Chem.AddHs(mol)
        embed_attempts = 1
        embed_id = AllChem.EmbedMolecule(mol, randomSeed=42)
        if embed_id == -1:
            embed_attempts = 2
            embed_id = AllChem.EmbedMolecule(mol, useRandomCoords=True, randomSeed=42)
        span.set("rdkit.embed_attempts", embed_attempts)
        span.set("rdkit.embed_status", int(embed_id))
        if embed_id == -1:
            span.set("tool.error", "embed_failed")
            return {
                "smiles": smiles,
                "ok": False,
                "error": "embed_failed",
            }
        # MMFFOptimizeMolecule returns 0 on convergence, 1 if not converged, -1 on failure.
        opt_status = AllChem.MMFFOptimizeMolecule(mol, maxIters=200)
        span.set("rdkit.optimization_status", int(opt_status))
        span.set("rdkit.optimization_iterations", 200 if opt_status == 1 else 0)
        # Energy from MMFF94 force field.
        energy = None
        try:
            props = AllChem.MMFFGetMoleculeProperties(mol)
            ff = AllChem.MMFFGetMoleculeForceField(mol, props)
            if ff is not None:
                energy = float(ff.CalcEnergy())
        except Exception:
            energy = None
        if energy is not None:
            span.set("tool.energy", energy)

        num_atoms = mol.GetNumAtoms()
        num_heavy_atoms = mol.GetNumHeavyAtoms()
        span.set("tool.num_atoms", num_atoms)
        span.set("tool.heavy_atom_count", num_heavy_atoms)

        conf = mol.GetConformer()
        coords = [
            (conf.GetAtomPosition(i).x, conf.GetAtomPosition(i).y, conf.GetAtomPosition(i).z)
            for i in range(num_atoms)
        ]
        result = {
            "smiles": smiles,
            "ok": True,
            "num_atoms": num_atoms,
            "num_heavy_atoms": num_heavy_atoms,
            "energy": energy,
            "embed_attempts": embed_attempts,
            "optimization_status": int(opt_status),
            "coords_summary": f"{num_atoms} atoms (3D), heavy={num_heavy_atoms}",
        }
        span.set("tool.output_size_bytes", len(json.dumps(result, default=str).encode("utf-8")))
        # Stash coords in result but keep span output_size_bytes the summary size
        # so trace JSON stays small.
        result["coords"] = coords
        return result


def compute_descriptors(smiles: str, obs: Observer) -> dict:
    """Compute MW, logP, TPSA, heavy-atom count, rotatable bonds for a SMILES.

    Pure RDKit, no I/O.
    """
    from rdkit import Chem
    from rdkit.Chem import Crippen, Descriptors, Lipinski

    with obs.span(
        "tool.compute_descriptors",
        **{
            "tool.name": "compute_descriptors",
            "tool.input_hash": input_hash(smiles),
            "tool.smiles": smiles,
            "tool.retry_count": 0,
        },
    ) as span:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            span.set("tool.error", "invalid_smiles")
            return {"smiles": smiles, "ok": False, "error": "invalid_smiles"}
        mw = float(Descriptors.MolWt(mol))
        logp = float(Crippen.MolLogP(mol))
        tpsa = float(Descriptors.TPSA(mol))
        heavy = int(Lipinski.HeavyAtomCount(mol))
        rotbonds = int(Lipinski.NumRotatableBonds(mol))
        span.set("tool.molecular_weight", mw)
        span.set("tool.logp", logp)
        span.set("tool.tpsa", tpsa)
        span.set("tool.heavy_atom_count", heavy)
        span.set("tool.num_rotatable_bonds", rotbonds)
        result = {
            "smiles": smiles,
            "ok": True,
            "molecular_weight": mw,
            "logp": logp,
            "tpsa": tpsa,
            "heavy_atom_count": heavy,
            "num_rotatable_bonds": rotbonds,
        }
        span.set("tool.output_size_bytes", len(json.dumps(result).encode("utf-8")))
        return result


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name).lower()


def _url_quote(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")


def _http_get_json(url: str, max_retries: int = 2) -> tuple[dict, int, int]:
    last_status = 0
    retries = 0
    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(timeout=_PUBCHEM_TIMEOUT) as client:
                r = client.get(url)
                last_status = r.status_code
                r.raise_for_status()
                return r.json(), retries, last_status
        except Exception:
            retries += 1
            if attempt == max_retries:
                return {}, retries, last_status
            time.sleep(0.5 * (attempt + 1))
    return {}, retries, last_status


def _extract_pubchem(result: dict) -> tuple[str, float | None]:
    try:
        props = result["PropertyTable"]["Properties"]
        if not props:
            return "", None
        first = props[0]
        smiles = first.get("CanonicalSMILES") or first.get("ConnectivitySMILES") or first.get("SMILES") or ""
        mw_raw = first.get("MolecularWeight")
        mw = float(mw_raw) if mw_raw is not None else None
        return str(smiles), mw
    except (KeyError, TypeError, ValueError):
        return "", None


__all__ = ["lookup_molecule", "smiles_to_3d", "compute_descriptors"]
