import argparse,json,sys,uuid
from pathlib import Path
from . import manager,contracts,storage

def failure_record(home,workspace,root,status,code):
    version='unknown'
    try:
        if workspace is not None:
            version=contracts.binding(contracts.read(Path(workspace)/'.prototype-workflow/requirement.json'))['distribution_version']
        else:
            version=contracts.read(root/'plugin.json')['version'];contracts.version(version)
    except (ValueError,KeyError,TypeError):pass
    payload={'status':status,'error':code,'distribution_version':version}
    record=home/'.local/share/prototype-workflow/errors'/f'{uuid.uuid4().hex}.json'
    try:storage.write_once(record,payload)
    except (OSError,ValueError):return {**payload,'record_status':'unavailable'}
    return {**payload,'record_path':str(record)}


def main(argv=None):
    parser=argparse.ArgumentParser(prog='prototype-workflow');commands=parser.add_subparsers(dest='command',required=True)
    for name in ('setup','new','resume','run','feedback'):
        sub=commands.add_parser(name);sub.add_argument('--home',type=Path,default=Path.home())
        if name=='setup':
            for option in ('plugin-root','company-profile','ssh-user','ssh-identity'):sub.add_argument('--'+option,required=True)
        else:sub.add_argument('--workspace',type=Path,required=True)
        if name=='new':sub.add_argument('--loaded-plugin-version',required=True)
        if name=='run':sub.add_argument('arguments',nargs=argparse.REMAINDER)
    args=parser.parse_args(argv);home=args.home.expanduser().resolve()
    root=Path(args.plugin_root).resolve() if args.command=='setup' else Path(__file__).resolve().parents[1]
    backend=manager.Backend(home,root)
    try:
        if args.command=='setup':result=manager.setup(home=home,plugin_root=root,company_profile=Path(args.company_profile),ssh_user=args.ssh_user,ssh_identity=args.ssh_identity,backend=backend)
        elif args.command=='new':result=manager.start_requirement(home=home,workspace=args.workspace,loaded_plugin_version=args.loaded_plugin_version,backend=backend)
        elif args.command=='resume':result=manager.resume_requirement(home=home,workspace=args.workspace,backend=backend)
        elif args.command=='run':
            status=manager.run_requirement(home=home,workspace=args.workspace,arguments=args.arguments[1:] if args.arguments[:1]==['--'] else args.arguments,backend=backend)
            if status:
                print(json.dumps(failure_record(home,args.workspace,root,'restart_required','NATIVE_COMMAND_FAILED'),ensure_ascii=False),file=sys.stderr)
            return status
        else:
            b=manager.resume_requirement(home=home,workspace=args.workspace,backend=backend)['binding']
            result={'distribution_version':b['distribution_version'],'completed':'','failed_step':'','stable_error_code':'','maintainer_involved':'','note':'人工填写；不要附业务原文、截图、代码或身份信息'}
        print(json.dumps(result,ensure_ascii=False));return 0
    except Exception as e:
        code=str(e) if isinstance(e,contracts.WorkflowError) else getattr(e,'error',type(e).__name__)
        if type(e) is ValueError and str(e).replace('_','').isalnum() and str(e).isupper():code=str(e)
        status=getattr(e,'status','blocked')
        if code=='CONFIGURATION_REQUIRED' or isinstance(e,FileNotFoundError):status='configuration_required'
        message={}
        if type(e).__name__=='KitError' and str(e).startswith(('需要 ','缺少 ','无法运行 ','此包仅验收')):
            status='configuration_required';code='ENVIRONMENT_REQUIRED';message={'message':str(e)}
        print(json.dumps({**failure_record(home,getattr(args,'workspace',None),root,status,code),'next_step':'核对本机初始化或保留现场联系维护者',**message},ensure_ascii=False),file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
