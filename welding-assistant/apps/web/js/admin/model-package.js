// Model management, Models tab: the download of a trained model. One press, no choices: the file holds the trained adapter, the base model it was trained on (Llama 3.2 1B) and how
// the training went (when, which dataset, the checks it passed). It holds no question pairs. Adding such a file to another computer is not offered in the interface for now.

// -> "" when the file was downloaded, or the reason it was not.
export async function downloadModel(model) {
  if (!model.dataset) return "This model has no trained adapter on this computer, so there is nothing to download.";
  const response = await fetch(`/api/admin/models/${encodeURIComponent(model.id)}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "model", pairs: false, gate: true, hand: false, specs: false }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    return data.error || "The file could not be made.";
  }
  const name = (response.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/);
  const link = document.createElement("a");
  link.href = URL.createObjectURL(await response.blob());
  link.download = name ? name[1] : "model.weldmodel";
  document.body.appendChild(link);
  link.click();
  link.remove();
  return "";
}
