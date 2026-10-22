import json
import shutil

path = "results/full_20x5_20261022T122009Z/attempts.jsonl"
out_path = "results/full_20x5_20261022T122009Z/attempts_fixed.jsonl"

with open(path, 'r') as f:
    lines = f.readlines()

with open(out_path, 'w') as f:
    for line in lines:
        if not line.strip():
            continue
        rec = json.loads(line)
        
        # ১. নতুন ফরম্যাটকে পুরনো ফরম্যাটে রূপান্তর (ড্যাশবোর্ডের জন্য)
        rec['status'] = 'repair_success' if rec.get('passed') else 'repair_failed'
        rec['evaluation_attempted'] = True
        rec['test_passed'] = rec.get('passed', False)
        # ✅ ড্যাশবোর্ড METHOD_ORDER-এ এই নামটাই খোঁজে
        rec['method'] = 'itms_ast_splice'
        
        # ২. মডেলের নাম যোগ করা (ড্যাশবোর্ডের জন্য)
        if not rec.get('requested_model_id'):
            rec['requested_model_id'] = rec.get('model', 'minimax/minimax-m3.1-flash:free')
        if not rec.get('served_model_id'):
            rec['served_model_id'] = rec.get('model', 'minimax/minimax-m3.1-flash:free')
        
        # ৩. টোকেনের হিসাব ঠিক করা (ড্যাশবোর্ড যাতে ভুল না দেখায়)
        if rec.get('input_tokens') is not None and rec.get('output_tokens') is not None:
            rec['total_tokens'] = rec['input_tokens'] + rec['output_tokens']
        
        # ৪. লেটেন্সি ঠিক করা (ড্যাশবোর্ড time দেখানোর জন্য)
        if rec.get('pipeline_duration_ms') is not None:
            rec['latency_ms'] = rec['pipeline_duration_ms']
        
        # ৫. Usage source যোগ করা (Mean token usage দেখানোর জন্য)
        if rec.get('input_tokens') is not None:
            rec['usage_source'] = 'provider_reported'
        else:
            rec['usage_source'] = 'missing'
        
        # ৬. Failure category যোগ করা
        if not rec.get('passed'):
            if rec.get('error_type') == 'LiveModeError':
                rec['failure_category'] = 'provider_error'
            elif rec.get('error_type') == 'AssertionError':
                rec['failure_category'] = 'test_failed'
            else:
                rec['failure_category'] = 'unknown'
        else:
            rec['failure_category'] = None

        f.write(json.dumps(rec) + "\n")

shutil.move(out_path, path)
print("✅ Fixed!")
