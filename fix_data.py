import json

path = "results/full_20x5_20261022T122009Z/attempts.jsonl"
out_path = "results/full_20x5_20261022T122009Z/attempts_fixed.jsonl"

with open(path, 'r') as f:
    lines = f.readlines()

with open(out_path, 'w') as f:
    for line in lines:
        if not line.strip(): continue
        rec = json.loads(line)
        
        # পুরনো ফরম্যাটের কী (keys) যোগ করা হচ্ছে
        rec['method'] = 'ITMS'
        rec['evaluation_attempted'] = True
        rec['test_passed'] = rec.get('passed', False)
        
        if rec.get('passed'):
            rec['status'] = 'repair_success'
        else:
            rec['status'] = 'repair_failed'
            
        f.write(json.dumps(rec) + "\n")

import shutil
shutil.move(out_path, path)
print("✅ ডেটা সফলভাবে কনভার্ট হয়েছে!")
