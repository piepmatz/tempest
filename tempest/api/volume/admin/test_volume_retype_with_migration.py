#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.


from oslo_log import log as logging

from tempest.api.volume import base
from tempest.common import waiters
from tempest import config
from tempest.lib import decorators

CONF = config.CONF

LOG = logging.getLogger(__name__)


class VolumeRetypeWithMigrationTest(base.BaseVolumeAdminTest):

    @classmethod
    def skip_checks(cls):
        super(VolumeRetypeWithMigrationTest, cls).skip_checks()

        if not CONF.volume_feature_enabled.multi_backend:
            raise cls.skipException("Cinder multi-backend feature disabled.")

        if len(set(CONF.volume.backend_names)) < 2:
            raise cls.skipException("Requires at least two different "
                                    "backend names")

    @classmethod
    def resource_setup(cls):
        super(VolumeRetypeWithMigrationTest, cls).resource_setup()
        # read backend name from a list.
        backend_src = CONF.volume.backend_names[0]
        backend_dst = CONF.volume.backend_names[1]

        extra_specs_src = {"volume_backend_name": backend_src}
        extra_specs_dst = {"volume_backend_name": backend_dst}

        cls.src_vol_type = cls.create_volume_type(extra_specs=extra_specs_src)
        cls.dst_vol_type = cls.create_volume_type(extra_specs=extra_specs_dst)

    def _wait_for_internal_volume_cleanup(self, vol):
        # When retyping a volume, Cinder creates an internal volume in the
        # target backend. The volume in the source backend is deleted after
        # the migration, so we need to wait for Cinder delete this volume
        # before deleting the types we've created.

        # This list should return 2 volumes until the copy and cleanup
        # process is finished.
        fetched_list = self.admin_volume_client.list_volumes(
            params={'all_tenants': True,
                    'display_name': vol['name']})['volumes']

        for fetched_vol in fetched_list:
            if fetched_vol['id'] != vol['id']:
                # This is the Cinder internal volume
                LOG.debug('Waiting for internal volume %s deletion',
                          fetched_vol['id'])
                self.admin_volume_client.wait_for_resource_deletion(
                    fetched_vol['id'])
                break

    def _retype_volume(self, volume):
        keys_with_no_change = ('id', 'size', 'description', 'name', 'user_id',
                               'os-vol-tenant-attr:tenant_id')
        keys_with_change = ('volume_type', 'os-vol-host-attr:host')

        volume_source = self.admin_volume_client.show_volume(
            volume['id'])['volume']

        self.volumes_client.retype_volume(
            volume['id'],
            new_type=self.dst_vol_type['name'],
            migration_policy='on-demand')
        self.addCleanup(self._wait_for_internal_volume_cleanup, volume)
        waiters.wait_for_volume_retype(self.volumes_client, volume['id'],
                                       self.dst_vol_type['name'])

        volume_dest = self.admin_volume_client.show_volume(
            volume['id'])['volume']

        # Check the volume information after the migration.
        self.assertEqual('success',
                         volume_dest['os-vol-mig-status-attr:migstat'])
        self.assertEqual('success', volume_dest['migration_status'])

        for key in keys_with_no_change:
            self.assertEqual(volume_source[key], volume_dest[key])

        for key in keys_with_change:
            self.assertNotEqual(volume_source[key], volume_dest[key])

    @decorators.idempotent_id('a1a41f3f-9dad-493e-9f09-3ff197d477cd')
    def test_available_volume_retype_with_migration(self):
        src_vol = self.create_volume(volume_type=self.src_vol_type['name'])
        self._retype_volume(src_vol)

    @decorators.idempotent_id('d0d9554f-e7a5-4104-8973-f35b27ccb60d')
    def test_volume_from_snapshot_retype_with_migration(self):
        # Create a volume in the first backend
        src_vol = self.create_volume(volume_type=self.src_vol_type['name'])

        # Create a volume snapshot
        snapshot = self.create_snapshot(src_vol['id'])

        # Create a volume from the snapshot
        src_vol = self.create_volume(volume_type=self.src_vol_type['name'],
                                     snapshot_id=snapshot['id'])

        # Delete the snapshot
        self.snapshots_client.delete_snapshot(snapshot['id'])
        self.snapshots_client.wait_for_resource_deletion(snapshot['id'])

        # Migrate the volume from snapshot to the second backend
        self._retype_volume(src_vol)
