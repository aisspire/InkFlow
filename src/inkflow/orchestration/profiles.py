"""Reusable local targets: revision CAS and immutable resolved configuration."""
from pathlib import Path
import json
from uuid import uuid4
from typing import Literal
from pydantic import Field, ValidationError
from inkflow.orchestration.definitions import Strict, schema_errors
from inkflow.workflow_assets.prompts import AssetValidationError
from inkflow.web.storage import encode, now


class Profile(Strict):
    name: str = Field(min_length=1, max_length=200)
    capability: Literal['local-blog'] = 'local-blog'
    repo_path: str = Field(min_length=1, max_length=2000)
    content_dir: str = Field(min_length=1, max_length=2000)
    format: Literal['astro-mdx'] = 'astro-mdx'
    naming: Literal['slug/index.mdx'] = 'slug/index.mdx'
    overwrite: Literal['confirm', 'never'] = 'confirm'


class PublishProfiles:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        with store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS publish_profiles(id TEXT PRIMARY KEY, generation INTEGER NOT NULL, revision TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS publish_profile_revisions(profile_id TEXT NOT NULL, revision TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(profile_id,revision));
            ''')

    def validate(self, value):
        from inkflow.services.publish import resolve_blog_destination
        from inkflow.settings import resolve_directory
        try:
            profile = Profile.model_validate(value).model_dump()
        except ValidationError as error:
            raise AssetValidationError(schema_errors(error, 'profile')) from None
        config, _, key = self.settings.snapshot()
        if key and key in encode(profile):
            raise ValueError('发布配置不能包含模型密钥。')
        repo = resolve_directory(profile['repo_path'], Path(config['_config_dir']))
        resolve_blog_destination(repo, profile['content_dir'], 'target-preview')
        profile['repo_path'] = str(repo)
        return profile

    def save(self, value, profile_id=None, generation=0):
        from inkflow.web.runs import ConflictError
        profile = self.validate(value)
        identity, revision = profile_id or 'profile-' + uuid4().hex, uuid4().hex
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if profile_id:
                changed = db.execute('UPDATE publish_profiles SET generation=generation+1,revision=? WHERE id=? AND generation=?', (revision, identity, generation))
                if changed.rowcount != 1:
                    raise ConflictError('发布配置已被修改，请加载最新修订比较后再保存。')
            else:
                db.execute('INSERT INTO publish_profiles VALUES (?,1,?)', (identity, revision))
            db.execute('INSERT INTO publish_profile_revisions VALUES (?,?,?,?)', (identity, revision, now(), encode(profile)))
        return self.get(identity)

    def revision(self, identity, revision):
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM publish_profile_revisions WHERE profile_id=? AND revision=?', (identity, revision)).fetchone()
        if not row:
            raise KeyError(revision)
        return {**dict(row), 'payload': json.loads(row['payload'])}

    def get(self, identity):
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM publish_profiles WHERE id=?', (identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            versions = [dict(v) for v in db.execute('SELECT revision,created_at FROM publish_profile_revisions WHERE profile_id=? ORDER BY rowid DESC', (identity,))]
            heads = list(db.execute('SELECT h.workflow_id,v.payload FROM workflow_heads h JOIN workflow_versions v ON v.id=h.version_id'))
        users = []
        for head in heads:
            package = json.loads(head['payload'])
            for node_id, node in package.get('compiled', {}).get('nodes', {}).items():
                if node.get('profile', {}).get('profile_id') == identity:
                    users.append({'workflow_id': head['workflow_id'], 'node_id': node_id, 'revision': node['profile']['revision']})
        return {**self.revision(identity, row['revision']), 'generation': row['generation'], 'revisions': versions, 'users': users}

    def list(self):
        with self.store.connection() as db:
            ids = [r['id'] for r in db.execute('SELECT id FROM publish_profiles ORDER BY rowid')]
        return [self.get(identity) for identity in ids]
