import re

def update_debug_html():
    with open("resources/debug.html", "r", encoding="utf-8") as f:
        content = f.read()

    # Update the manual task dropdown to add startup lock if missing, or just handle clear_emergency_stop
    old_add_task = """    async function addManualTask() {
        const name   = document.getElementById('manualTaskName').value;
        const target = document.getElementById('manualTaskTarget').value.trim();
        const task   = { name, args:{}, question:'Manual entry from monitor' };
        if (name === 'locate_object')         task.args.target_name   = target;
        if (name === 'pick_and_place_object') task.args.object_name   = target;
        if (name === 'relocate_object')       task.args.obstacle_name = target;
        await fetch('/queue-add', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({task}) });
        document.getElementById('manualTaskTarget').value = '';
    }"""
    
    new_add_task = """    async function addManualTask() {
        const name   = document.getElementById('manualTaskName').value;
        const target = document.getElementById('manualTaskTarget').value.trim();
        
        if (name === 'clear_emergency_stop') {
            await fetch('/clear-emergency-stop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ manual_confirmed: true })
            });
            return;
        }
        
        if (name === 'clear_startup_lock') {
            await fetch('/clear-startup-lock', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ manual_confirmed: true })
            });
            return;
        }

        const task   = { name, args:{}, question:'Manual entry from monitor' };
        if (name === 'locate_object')         task.args.target_name   = target;
        if (name === 'pick_and_place_object') task.args.object_name   = target;
        if (name === 'relocate_object')       task.args.obstacle_name = target;
        await fetch('/queue-add', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({task}) });
        document.getElementById('manualTaskTarget').value = '';
    }"""
    
    content = content.replace(old_add_task, new_add_task)
    
    # Add clear startup lock to the dropdown if not present
    if '<option value="clear_startup_lock">' not in content:
        content = content.replace('<option value="clear_emergency_stop">', '<option value="clear_startup_lock">dY"" Clear Startup Lock</option>\n                    <option value="clear_emergency_stop">')
        
    with open("resources/debug.html", "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    update_debug_html()
