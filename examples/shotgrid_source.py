"""Example: published files from ShotGrid as a gallery data source.

    from shotgun_api3 import Shotgun
    from ilp_gallery import dcc
    from shotgrid_source import ShotGridPublishes

    sg = Shotgun("https://ilpvfx.shotgrid.autodesk.com", script_name="gallery", api_key="...")
    b = dcc.show([])                                   # empty browser, then add the source
    b.add_source(ShotGridPublishes(sg, project_id=123, types=["Model", "Rig"]))

It only uses documented ``shotgun_api3`` calls (``find``) and plain HTTP for the
thumbnails, and works without a ShotGrid connection in the tests via a fake.
"""

from __future__ import annotations

import datetime as dt
import urllib.request

from ilp_gallery.core import Changes, DataSource, make_record

FIELDS = ["code", "path", "entity", "published_file_type", "version_number", "image",
          "tags", "updated_at", "created_by", "description", "sg_status_list", "project"]


class ShotGridPublishes(DataSource):
    name = "shotgrid"
    label = "ShotGrid publishes"
    poll_interval = 60.0          # ask ShotGrid for changes once a minute

    def __init__(self, sg, project_id: int, types: list[str] | None = None,
                 status: list[str] | None = None, full_reload_every: int = 30) -> None:
        super().__init__()
        self.sg = sg
        self.project_id = project_id
        self.types = types
        self.status = status
        self.full_reload_every = full_reload_every  # retired publishes only disappear on a full load
        self._since: dt.datetime | None = None
        self._polls = 0

    # -- helpers ----------------------------------------------------------
    def _filters(self, since=None) -> list:
        f = [["project", "is", {"type": "Project", "id": self.project_id}]]
        if self.types:
            f.append(["published_file_type.PublishedFileType.code", "in", self.types])
        if self.status:
            f.append(["sg_status_list", "in", self.status])
        if since is not None:
            f.append(["updated_at", "greater_than", since])
        return f

    def _record(self, pub: dict) -> dict:
        entity = pub.get("entity") or {}
        ptype = (pub.get("published_file_type") or {}).get("name") or "Publish"
        path = (pub.get("path") or {}).get("local_path") or ""
        return make_record(
            path or f"shotgrid://PublishedFile/{pub['id']}",
            id=f"sg-pub-{pub['id']}",
            name=pub.get("code") or f"Publish {pub['id']}",
            categories=[ptype, entity.get("name", "")],
            tags=[t.get("name") for t in pub.get("tags") or [] if t.get("name")],
            description=pub.get("description") or "",
            type_label=ptype,
            category_paths=[[entity.get("type", "Entity"), entity.get("name", "?"), ptype]],
            display=[("Version", pub.get("version_number")),
                     ("Status", pub.get("sg_status_list")),
                     ("Artist", (pub.get("created_by") or {}).get("name")),
                     ("Updated", str(pub.get("updated_at") or ""))],
            sg_id=pub["id"], image_url=pub.get("image"), entity_id=entity.get("id"),
        )

    # -- DataSource API -----------------------------------------------------
    def load(self):
        self._since = dt.datetime.now(dt.timezone.utc)
        pubs = self.sg.find("PublishedFile", self._filters(), FIELDS, order=[{"field_name": "code",
                                                                             "direction": "asc"}])
        return [self._record(p) for p in pubs]

    def poll(self):
        self._polls += 1
        if self._polls % self.full_reload_every == 0:
            return Changes(reset=True)
        since, self._since = self._since, dt.datetime.now(dt.timezone.utc)
        if since is None:
            return None
        pubs = self.sg.find("PublishedFile", self._filters(since), FIELDS)
        return Changes(updated=[self._record(p) for p in pubs]) if pubs else None

    def thumbnail(self, item):
        url = item.metadata.get("image_url")
        if not url:
            return None
        with urllib.request.urlopen(url, timeout=10) as r:  # ShotGrid thumbnail URLs are pre-signed
            return r.read()

    # writable: tags go back to ShotGrid
    def set_tags(self, item, tags):
        existing = {t["name"]: t for t in self.sg.find("Tag", [["name", "in", tags]], ["name"])}
        tag_refs = [{"type": "Tag", "id": existing[t]["id"]} for t in tags if t in existing]
        self.sg.update("PublishedFile", item.metadata["sg_id"], {"tags": tag_refs})
