const editor = document.getElementById("editor");
const status = document.getElementById("status");

// Load JSON from backend and highlight
async function loadConfig() {
	const response = await fetch("/api/config");
	const text = await response.text();
	editor.innerHTML = highlightJSON(text);
}

// Save JSON to backend
async function saveConfig() {
	try {
		const plainText = editor.innerText;
		const json = JSON.parse(plainText);
		const res = await fetch("/api/config", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(json)
		});
		const data = await res.json();
		status.textContent = "✅ Saved successfully!";
		status.className = "text-green-600 text-sm font-semibold";
	} catch (err) {
		status.textContent = "❌ Invalid JSON or failed to save.";
		status.className = "text-red-600 text-sm font-semibold";
	}
}

// Highlight JSON syntax
function highlightJSON(json) {
	try {
		const obj = JSON.parse(json);
		json = JSON.stringify(obj, null, 2);
	} catch (e) {
		return `<span class="text-red-600">Invalid JSON</span>`;
	}

	return json.replace(/("(\\u[a-fA-F\d]{4}|\\[^u]|[^\\"])*"(?:\s*:)?|\b(true|false|null)\b|\d+)/g, (match) => {
		let cls = "number";
		if (/^"/.test(match)) {
			if (/:$/.test(match)) {
				cls = "key";
			} else {
				cls = "string";
			}
		} else if (/true|false/.test(match)) {
			cls = "boolean";
		} else if (/null/.test(match)) {
			cls = "null";
		}
		return `<span class="token ${cls}">${match}</span>`;
	});
}

// Auto-highlight on input
editor.addEventListener("input", () => {
	const text = editor.innerText;
	const sel = saveCursor();
	editor.innerHTML = highlightJSON(text);
	restoreCursor(sel);
});

// Cursor preservation
function saveCursor() {
	const selection = window.getSelection();
	if (selection.rangeCount === 0) return null;
	const range = selection.getRangeAt(0);
	return { range, start: range.startOffset, node: range.startContainer };
}

function restoreCursor(saved) {
	if (!saved) return;
	const selection = window.getSelection();
	const range = document.createRange();
	range.setStart(saved.node, Math.min(saved.start, saved.node.length));
	range.collapse(true);
	selection.removeAllRanges();
	selection.addRange(range);
}

window.onload = loadConfig;
