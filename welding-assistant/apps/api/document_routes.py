"""The document library, for the server (Managers and administrators; after auth_routes.handle).

    GET  /api/admin/documents                  every document with new / changed / current, what was removed, and the re-index status
    POST /api/admin/documents/upload           multipart form of .docx files. A file with the name of an existing one replaces it. They are saved only: nothing is indexed until someone presses Reindex.
    POST /api/admin/documents/delete           {names}  moves the documents out of the library (into a trash folder). Nothing is indexed until someone presses Reindex.
    POST /api/admin/documents/refresh          Reindex: read the documents again, make the question pairs again and clear the pairs written by hand. Refused when no document is new, changed or removed.
    POST /api/admin/documents/refresh/cancel   stop it; the live index is not changed

Every state says how many pairs written by hand a reindex would delete (`hand_pairs`), so the page can warn before it starts. Indexing needs a retrain to be finished (both use the same
index and GPU).
"""
from welding_ai.accounts import levels
from welding_ai.ingestion import uploads

MAX_REQUEST = uploads.MAX_FILES * uploads.MAX_FILE + 1024 * 1024


def handle(handler, docs_dir, trash, manifest, job, method, path, body, request_index, retraining=lambda: False, hand_pairs=lambda: 0):
    """request_index() is kept for callers that want indexing after a retrain; hand_pairs() -> how many question pairs written by hand a reindex deletes."""
    if not path.startswith("/api/admin/documents"):
        return False
    # each route has its own right (Library screen controls): saving needs "library" (2.2), deleting "doc_delete" (2.3), reindexing and cancelling it "reindex" (2.4); the state
    # (what is waiting, and how many pairs written by hand a reindex deletes) is open to anyone who may do one of them, because the Library page needs it for its warning
    right = {("GET", "/api/admin/documents"): ("library", "reindex", "doc_delete"), ("POST", "/api/admin/documents/upload"): ("library",),
             ("POST", "/api/admin/documents/delete"): ("doc_delete",), ("POST", "/api/admin/documents/refresh"): ("reindex",),
             ("POST", "/api/admin/documents/refresh/cancel"): ("reindex",)}.get((method, path))
    if right is None:
        handler.send_bytes(404, b"not found", "text/plain")
        return True
    if not any(levels.allowed(handler.user["level"], r) for r in right):
        handler.send_json({"error": "You are not allowed to do that."}, 403)
        return True

    def state(**extra):
        return {**manifest.detect(), "job": job.status(), "retraining": retraining(), "hand_pairs": hand_pairs(), **extra}

    if path == "/api/admin/documents" and method == "GET":
        handler.send_json(state())
        return True

    if path == "/api/admin/documents/upload" and method == "POST":
        try:
            files = uploads.parse_multipart(handler.headers.get("Content-Type", ""), handler.raw_body)
            if not files:
                raise uploads.UploadError("No files were sent.")
            result = uploads.save_all(docs_dir, files, replace=True)
        except uploads.UploadError as e:
            handler.send_json({"error": e.message}, 400)
            return True
        handler.send_json(state(**result), 200 if result["saved"] else 400)
        return True

    if path == "/api/admin/documents/delete" and method == "POST":
        names = (body or {}).get("names")
        if not isinstance(names, list) or not names:
            handler.send_json({"error": "Send the names of the documents to delete."}, 400)
            return True
        result = uploads.delete_all(docs_dir, [str(n) for n in names][:200], trash)
        handler.send_json(state(**result))
        return True

    if path == "/api/admin/documents/refresh" and method == "POST":
        if retraining():
            handler.send_json({"error": "A retrain is running. Reindex when it has finished."}, 409)
        elif not manifest.detect()["pending"]:
            handler.send_json({"error": "Every document is already indexed. Reindex is for when documents were added, changed or removed."}, 409)
        elif not job.start():
            handler.send_json({"error": "A re-index is already running."}, 409)
        else:
            handler.send_json({"job": job.status()}, 202)
        return True

    if path == "/api/admin/documents/refresh/cancel" and method == "POST":
        job.cancel()
        handler.send_json({"job": job.status()})
        return True

    handler.send_bytes(404, b"not found", "text/plain")
    return True
