export async function uploadCSVs(oldFile: File, newFile: File) {
  const formData = new FormData();
  formData.append("old_csv", oldFile);
  formData.append("new_csv", newFile);

  const response = await fetch("http://127.0.0.1:5001/api/process", {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`Failed to upload CSVs: ${response.status}`);
  }

  return await response.json();
}