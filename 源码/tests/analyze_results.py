"""分析降级测试结果"""
import json
import sys

filepath = sys.argv[1] if len(sys.argv) > 1 else 'degradation_test_results_20260520_210145.json'

with open(filepath, 'r', encoding='utf-8') as f:
    data = json.load(f)

results = data['results']
print(f"测试时间: {data.get('test_time', '?')}")
print(f"总题数: {len(results)}")

completed = [r for r in results if r.get('status') == 'completed']
failed = [r for r in results if r.get('status') == 'failed']
running = [r for r in results if r.get('status') == 'running']
pending = [r for r in results if r.get('status') == 'pending']

print(f"已完成: {len(completed)}")
print(f"失败: {len(failed)}")
print(f"运行中: {len(running)}")
print(f"待处理: {len(pending)}")
print()

# 澄清准确率
a_group = [r for r in completed if r.get('clarify_expected') == True]
b_group = [r for r in completed if r.get('clarify_expected') == False]
clarify_hit = sum(1 for r in a_group if r.get('clarify_triggered'))
clarify_miss = len(a_group) - clarify_hit
b_false_positive = sum(1 for r in b_group if r.get('clarify_triggered'))
b_correct = len(b_group) - b_false_positive

print(f"=== 澄清准确率 ===")
print(f"A组(预期触发澄清): {len(a_group)}题, 实际触发: {clarify_hit}, 未触发: {clarify_miss}")
print(f"  准确率: {clarify_hit}/{len(a_group)} = {round(clarify_hit/len(a_group)*100,1)}%")
print(f"B组(预期不触发): {len(b_group)}题, 误触发: {b_false_positive}, 正确: {b_correct}")
print(f"  准确率: {b_correct}/{len(b_group)} = {round(b_correct/len(b_group)*100,1)}%")
total_acc = (clarify_hit + b_correct) / len(completed) * 100
print(f"综合准确率: {clarify_hit + b_correct}/{len(completed)} = {round(total_acc,1)}%")
print()

# 降级率
degrade_count = sum(1 for r in completed if r.get('degradation', {}).get('has_degradation'))
print(f"=== 降级率 ===")
print(f"降级: {degrade_count}/{len(completed)} = {round(degrade_count/len(completed)*100,1)}%")
print()

# 耗时统计
times = [r.get('duration_seconds', 0) for r in completed if r.get('duration_seconds')]
if times:
    print(f"=== 耗时统计 ===")
    print(f"平均耗时: {round(sum(times)/len(times),1)}s")
    print(f"最快: {round(min(times),1)}s")
    print(f"最慢: {round(max(times),1)}s")
    print()

# 输出题数统计
q_counts = [len(r.get('output_questions', [])) for r in completed]
print(f"=== 输出问题数 ===")
print(f"平均: {round(sum(q_counts)/len(q_counts),1)}")
print(f"最小: {min(q_counts)}, 最大: {max(q_counts)}")
print()

# 逐题明细
print(f"{'ID':5s} {'域名':10s} {'状态':6s} {'澄清预期':6s} {'澄清实际':6s} {'降级':4s} {'输出题数':6s} {'耗时':>8s}")
print("-" * 80)
for r in results:
    sid = r.get('id', '?')
    domain = r.get('domain', '?')
    status = r.get('status', '?')
    exp = 'Yes' if r.get('clarify_expected') else 'No '
    got = 'Yes' if r.get('clarify_triggered') else 'No '
    deg = 'Yes' if r.get('degradation', {}).get('has_degradation') else 'No '
    qcnt = len(r.get('output_questions', [])) if status == 'completed' else '-'
    dur = f"{r.get('duration_seconds', 0):.1f}s" if r.get('duration_seconds') else '-'
    
    # 标记纠正结果
    flag = ''
    if status == 'completed':
        if exp.strip() == 'Yes' and got.strip() == 'Yes':
            flag = ' ✅'
        elif exp.strip() == 'Yes' and got.strip() == 'No':
            flag = ' ❌漏报'
        elif exp.strip() == 'No' and got.strip() == 'Yes':
            flag = ' ⚠️误报'
        else:
            flag = ' ✅'
    
    print(f"{sid:5s} {domain:10s} {status:6s} {exp:6s} {got:6s} {deg:4s} {str(qcnt):>6s} {dur:>8s}{flag}")

# A组未触发澄清的题目
print()
print("=== A组未触发澄清 (漏报) ===")
for r in completed:
    if r.get('clarify_expected') and not r.get('clarify_triggered'):
        print(f"  {r['id']}: {r.get('input','?')} [{r.get('category','?')}]")

# B组误触发澄清的题目
print()
print("=== B组误触发澄清 ===")
for r in completed:
    if not r.get('clarify_expected') and r.get('clarify_triggered'):
        print(f"  {r['id']}: {r.get('input','?')} [{r.get('category','?')}]")
