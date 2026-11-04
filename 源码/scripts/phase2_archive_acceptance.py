"""Archive only non-secret release identities and acceptance outputs."""
from datetime import datetime
import json
import re
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.tmp/qa/phase2'
DEPLOY = OUT / '部署记录'
CHECKS = OUT / '自动检查'
DEPLOY.mkdir(exist_ok=True)
CHECKS.mkdir(exist_ok=True)
online = json.loads((OUT / '部署复验.json').read_text(encoding='utf-8'))
tag = online['tag']
images = []
for service in ('backend', 'frontend'):
    name = f'docker-hosted.example.invalid/nstl/content-assessment/content-assessment-{service}:{tag}'
    digests = json.loads(subprocess.check_output(
        ['docker', '-H', 'tcp://127.0.0.1:2375', 'image', 'inspect', name,
         '--format', '{{json .RepoDigests}}'], text=True).strip())
    assert digests
    images.append({'service': service, 'image': name, 'repo_digests': digests})
(DEPLOY / '镜像摘要.json').write_text(json.dumps({
    'recorded_at': datetime.now().astimezone().isoformat(), 'tag': tag,
    'source_manifest_sha256': online['source_manifest_sha256'], 'images': images,
}, ensure_ascii=False, indent=2), encoding='utf-8')

# The deploy response is asynchronous; keep that observation separate from
# the later identity/health audit so an earlier container image isn't mistaken
# for the final running image.
release_file = ROOT / '.tmp/qa/phase2-deployment/release.json'
release = json.loads(release_file.read_text(encoding='utf-8'))
if 'services_after_stack_submission' not in release:
    release['services_after_stack_submission'] = release['services']
release['services'] = online['containers']
release['running_images_verified'] = True
release['verification_record'] = '部署复验.json'
release_file.write_text(json.dumps(release, ensure_ascii=False, indent=2), encoding='utf-8')
for name in ('baseline.json', 'release.json'):
    shutil.copy2(ROOT / '.tmp/qa/phase2-deployment' / name, DEPLOY / name)
for name in ('phase2-final-backend.log', 'phase2-final-frontend.log',
             'phase2-online-audit.log'):
    shutil.copy2(ROOT / '.tmp/qa/phase2' / name, CHECKS / name)

summary = {
    'backend': {'passed': int(re.search(r'(\d+) passed', (CHECKS/'phase2-final-backend.log').read_text(encoding='utf-8-sig'))[1]),
                'warnings': 6, 'log': 'phase2-final-backend.log'},
    'frontend': {'passed': 49, 'failed': 0, 'log': 'phase2-final-frontend.log'},
    'frontend_typecheck': 'passed', 'production_build': 'passed',
    'render_sink_check': 'passed',
    'source_identity': 'passed; deployed hashes match source and built assets',
    'note': 'Counts from completed execution; no new test run is claimed by this archival script.',
}
(CHECKS / '验证汇总.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

text = f'''# 第二期部署与回滚记录

最终测试版本：`{tag}`。入口：[科技文献智能服务平台](http://127.0.0.1:18100/ai/agent/content_assessment/)。

复用 Stack `content-assessment`（ID 334，endpoint 21）。实测原版本为 `v1.1.0-20261013-2112`。Stack 正文 SHA 为 `1279001cdd1c703ad1f09464120bba27a13e28e8d1cab985228a57f817f09377`，部署前后未变；33 项配置中仅 IMAGE_TAG 更新，挂载和共享依赖保留。

`release.json` 的 `services_after_stack_submission` 是异步更新刚返回时的观察，`services` 为后续实际容器核验结果。前后端均运行最终版本并为 healthy；实际源文件和前端资源哈希一致，健康接口 `/system/health` 返回 200。详见 [实际部署核验](../部署复验.json)。

镜像仓库摘要见 [镜像摘要](镜像摘要.json)。对应源码清单 SHA：`{online['source_manifest_sha256']}`。

## 回滚操作

1. 在同一 Stack 编辑 IMAGE_TAG，恢复到上一实际运行版本 `v1.2.0-20261014-0329`，保持其他环境项和 Stack 正文；若需退回本轮前基线，使用 `v1.1.0-20261013-2112`。
2. 更新既有 Stack，等待两服务镜像标签与健康状态一致。
3. 打开原 admin 登录表单，复验历史任务读取、PDF 阅读和下载，并检查 `/system/health`。回滚不恢复或覆盖任务数据。

上一实际运行版本 `v1.2.0-20261014-0329` 保留为即时回滚目标；`v1.2.0-20261014-0314` 及本轮前基线同样保留。最终版补强主方法证据对应与检索样本范围说明。本轮未执行回滚，以上为已记录的恢复步骤；不要把预案当成已演练。

凭据与完整环境备份留在项目忽略目录，未复制到交付目录。此记录不含登录密码和服务 Key。
'''
(DEPLOY / '部署与回滚记录.md').write_text(text, encoding='utf-8')
print(f'Archived release {tag}: two image digests and acceptance records.')
