const test=require('node:test');const assert=require('node:assert/strict');
const q=require('../skills/enterprise-prototype-code-query/lib/ssh.js');
test('bounded public scripts',()=>{
 for(const script of ['/srv/query.js','/opt/team/run.js'])assert.equal(q.buildRemoteCommand(script,'list',[]),`node '${script}' 'list'`);
 for(const script of ['/tmp/x.js;id','/tmp/../x.js','/a//b.js','x.js'])assert.throws(()=>q.buildRemoteCommand(script,'list',[]));
 assert.throws(()=>q.buildRemoteCommand('/srv/query.js','exec',['id']));
});
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
function setup(t,host){
 const home=fs.mkdtempSync(path.join(os.tmpdir(),'public-query-'));t.after(()=>fs.rmSync(home,{recursive:true,force:true}));
 fs.mkdirSync(path.join(home,'.ssh'));const identity=path.join(home,'.ssh','fixture');fs.writeFileSync(identity,'not a key',{mode:0o600});
 const knownHosts=path.join(home,'known_hosts');fs.writeFileSync(knownHosts,host+' ssh-ed25519 synthetic\n',{mode:0o400});
 const service={schema_version:2,host,port:22,remote_script:'/srv/query.js',known_hosts_sha256:crypto.createHash('sha256').update(fs.readFileSync(knownHosts)).digest('hex')};
 const serviceConfig=path.join(home,'service.json');fs.writeFileSync(serviceConfig,JSON.stringify(service),{mode:0o400});
 const profile=path.join(home,'profile.json');fs.writeFileSync(profile,JSON.stringify({schema_version:1,service,known_hosts:fs.readFileSync(knownHosts,'utf8')}),{mode:0o400});
 const userConfig=path.join(home,'user.json');fs.writeFileSync(userConfig,JSON.stringify({schema_version:1,ssh_user:'fixture.user',ssh_identity:identity}),{mode:0o600});
 return {home,profile,serviceConfig,knownHosts,userConfig,companyProfileSha256:crypto.createHash('sha256').update(fs.readFileSync(profile)).digest('hex')};
}
test('two trusted synthetic services retain one bounded list',t=>{
 for(const host of ['192.0.2.10','198.51.100.20']){const f=setup(t,host);let count=0;
 const result=q.runProbe(f,{home:f.home,spawnSync:(program,args)=>{count++;assert.equal(program,'ssh');assert.ok(args.includes('fixture.user@'+host));return {status:0,stdout:'{"success":true,"projects":{}}'};}});
 assert.equal(result.status,'pass');assert.equal(count,1);}
});
test('profile replacement during probe cannot produce success', t => {
 const f=setup(t,'192.0.2.10');
 assert.throws(() => q.runProbe(f, {
   home:f.home,
   spawnSync:() => {
     fs.chmodSync(f.profile,0o600);
     fs.appendFileSync(f.profile,' ');
     fs.chmodSync(f.profile,0o400);
     return {status:0,stdout:'{"success":true,"projects":{}}'};
   }
 }), e => e.code === 'host_identity_blocked');
});
