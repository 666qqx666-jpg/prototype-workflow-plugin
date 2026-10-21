from __future__ import annotations
import argparse, json
from pathlib import Path
import yaml
from enterprise_prd.canonical import canonical_json, sha256_bytes, sha256_file
from enterprise_prd.baseline_registry.snapshot import RegistrySnapshot
from enterprise_prd.baseline_registry.manifest import ManifestStore
from enterprise_prd.local_context.adapters.fixture import FixtureSourceReader
from enterprise_prd.local_snapshot.reader import LocalSnapshotReader
from enterprise_prd.local_snapshot.snapshot import SnapshotStore
from .builder import build_preview, resolve_production_baseline
from .bridge import TaskKnowledgeBridge
from .store import TaskKnowledgeStore, commit_manifest, exclusive_private_json
from .writer_input import build_writer_input_snapshot

def absolute(path: str) -> Path:
    value=Path(path)
    if not value.is_absolute() or value.is_symlink(): raise ValueError('private path is invalid')
    return value.resolve()
def load_json(path: str) -> dict: return json.loads(absolute(path).read_text())
def readers(args):
    if (args.fixture_sources is None) == (args.snapshot_root is None): raise ValueError('exactly one local reader configuration is required')
    if args.fixture_sources: return {'fixture':FixtureSourceReader.load(absolute(args.fixture_sources))}
    return {'local_snapshot': LocalSnapshotReader(SnapshotStore(absolute(args.snapshot_root)))}
def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
    for name in ('preview','commit-manifest'):
        q=sub.add_parser(name); q.add_argument('--registry',required=True); q.add_argument('--draft',required=True)
        if name=='preview': q.add_argument('--baseline-root',required=True); q.add_argument('--preview-output',required=True); q.add_argument('--created-by',required=True); q.add_argument('--created-at',required=True)
        else:
            q.add_argument('--baseline-manifest',required=True); q.add_argument('--preview',required=True); q.add_argument('--confirmation',required=True); q.add_argument('--task-root',required=True)
    for name in ('prepare-outlines','confirm-sections','issue-grant','assemble-context'):
        q=sub.add_parser(name); q.add_argument('--manifest',required=True); q.add_argument('--registry',required=True); q.add_argument('--runtime-root',required=True); q.add_argument('--session-id',required=True); q.add_argument('--fixture-sources'); q.add_argument('--snapshot-root'); q.add_argument('--sections'); q.add_argument('--index')
        if name == 'assemble-context': q.add_argument('--grant', required=True)
    q=sub.add_parser('create-writer-input');
    for key in ('run-id','enterprise-snapshot','manifest','context','receipt','index','pack','created-at','task-root'): q.add_argument('--'+key,required=True)
    return p
def main(argv=None) -> int:
    args=parser().parse_args(argv)
    try:
        if args.command=='preview':
            snapshot=RegistrySnapshot.load_yaml(absolute(args.registry)); draft=yaml.safe_load(absolute(args.draft).read_text()); baseline=resolve_production_baseline(snapshot,task_label=draft['task']['task_label'],surface_ids=tuple(draft['task']['target_surface_ids']),created_by=args.created_by,created_at=args.created_at); baseline_path=ManifestStore(absolute(args.baseline_root)).save(baseline); output=absolute(args.preview_output); exclusive_private_json(output,build_preview(snapshot,baseline,draft),'preview_conflict'); print(json.dumps({'preview_sha256':sha256_file(output), 'baseline_manifest_id':baseline['manifest_id'], 'baseline_manifest_sha256':sha256_file(baseline_path)})); return 0
        if args.command=='commit-manifest':
            snapshot=RegistrySnapshot.load_yaml(absolute(args.registry)); draft=yaml.safe_load(absolute(args.draft).read_text()); baseline=load_json(args.baseline_manifest); preview=load_json(args.preview); confirmation=load_json(args.confirmation); result=commit_manifest(snapshot,baseline,draft,preview,confirmation,TaskKnowledgeStore(absolute(args.task_root))); print(json.dumps({'manifest_id':result['manifest']['manifest_id'],'created':result['created']})); return 0
        if args.command in {'prepare-outlines','confirm-sections','issue-grant','assemble-context'}:
            snapshot=RegistrySnapshot.load_yaml(absolute(args.registry)); manifest=load_json(args.manifest); bridge=TaskKnowledgeBridge(absolute(args.runtime_root),snapshot,manifest,readers(args))
            if args.command=='prepare-outlines': result=bridge.prepare_outlines(args.session_id); print(json.dumps({'session_id':args.session_id,'status':result['status']})); return 0
            if args.command=='confirm-sections': result=bridge.confirm_role_sections(args.session_id,yaml.safe_load(absolute(args.sections).read_text())); print(json.dumps({'status':'sections_confirmed','index_sha256':sha256_file(result.role_section_index_path)})); return 0
            index=absolute(args.index)
            if args.command=='issue-grant': path=bridge.issue_grant(args.session_id,index); print(json.dumps({'grant_sha256':sha256_file(path)})); return 0
            path=bridge.assemble_context(absolute(args.grant),index); print(json.dumps({'context_sha256':sha256_file(path)})); return 0
        value=build_writer_input_snapshot(run_id=args.run_id,enterprise_snapshot_path=absolute(args.enterprise_snapshot),manifest_path=absolute(args.manifest),context_path=absolute(args.context),receipt_path=absolute(args.receipt),role_index_path=absolute(args.index),pack_path=absolute(args.pack),created_at=args.created_at); path,_=TaskKnowledgeStore(absolute(args.task_root)).save_writer_input(value); print(json.dumps({'run_id':args.run_id,'snapshot_sha256':sha256_file(path)})); return 0
    except Exception as exc:
        print(str(exc),file=__import__('sys').stderr); return 2
if __name__=='__main__': raise SystemExit(main())
