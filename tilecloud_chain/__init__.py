# -*- coding: utf-8 -*-

import logging
import yaml
from itertools import imap, ifilter
from hashlib import sha1
from cStringIO import StringIO
#from multiprocessing import Pool
#from multiprocessing.pool import ApplyResult
#from multiprocessing.pool import ThreadPool as Pool
from multiprocessing.dummy import Pool

try:
    from PIL import Image
except:
    import Image

import psycopg2
from shapely.wkt import loads as loads_wkt
from shapely.geometry import Polygon
import boto.sqs
from boto.sqs.jsonmessage import JSONMessage

from tilecloud import Tile, BoundingPyramid
from tilecloud.grid.free import FreeTileGrid
from tilecloud.filter.error import LogErrors, MaximumConsecutiveErrors, DropErrors


class TileGeneration:
    geom = None

    def __init__(self, config_file, options, layer_name=None):
        self.config = yaml.load(file(config_file))

        self.grids = self.config['grids']
        for gname, grid in self.config['grids'].items():
            scale = int(grid.get('resolution_scale', 1))
            grid['obj'] = FreeTileGrid(
                resolutions=[int(round(r * scale)) for r in grid['resolutions']],
                scale=scale,
                max_extent=grid['bbox'],
                tile_size=grid.get('tile_size', 256))

        default = self.config['layer_default']
        self.layers = {}
        for lname, layer in self.config['layers'].items():
            layer_object = {}
            for k, v in default.items():
                layer_object[k] = v

            for k, v in layer.items():
                layer_object[k] = v

            layer_object['grid_ref'] = self.grids[layer_object['grid']]

            self.layers[lname] = layer_object

        self.layer = None
        if layer_name:
            self.set_layer(layer_name, options)

        self.caches = self.config['caches']

        self.pool = None
        if 'number_process' in self.config['generation']:
            self.pool = Pool(self.config['generation']['number_process'])

    def set_layer(self, layer, options):
        self.layer = self.layers[layer]

        if options.bbox:
            self.init_geom(options.bbox)
        elif 'bbox' in self.layer:
            self.init_geom(self.layer['bbox'])
        else:
            self.init_geom(self.layer['grid_ref']['bbox'])

    def get_grid(self, name=None):
        if not name:
            name = self.layer['grid']

        return self.grids[name]

    def get_sqs_queue(self):
        config_sqs = self.config['forge']['sqs']
        connection = boto.sqs.connect_to_region(config_sqs['region_name'])
        queue = connection.create_queue(config_sqs['queue_name'])
        queue.set_message_class(JSONMessage)
        return queue

    def init_geom(self, extent=None):
        self.geom = None
        if 'connection' in self.layer and 'sql' in self.layer:
            conn = psycopg2.connect(self.layer['connection'])
            cursor = conn.cursor()
            cursor.execute("SELECT ST_AsText((SELECT " +
                self.layer['sql'].strip('" ') + "))")
            self.geom = loads_wkt(cursor.fetchone()[0])
        elif extent:
            self.geom = Polygon((
                    (extent[0], extent[1]),
                    (extent[0], extent[3]),
                    (extent[2], extent[3]),
                    (extent[2], extent[1]),
                ))

    def add_geom_filter(self):
        if self.geom:
            self.ifilter(IntersectGeometryFilter(
                    grid=self.get_grid(),
                    geom=self.geom))

    def add_error_filters(self, logger):
        self.imap(LogErrors(logger, logging.ERROR,
                "Error in tile: %(tilecoord)s, %(error)r"))
        if 'maxconsecutive_errors' in self.config['generation']:
            self.imap(MaximumConsecutiveErrors(
                    self.config['generation']['maxconsecutive_errors']))
        self.ifilter(DropErrors())

    def init_tilecoords(self, options):
        bounding_pyramid = BoundingPyramid(tilegrid=self.layer['grid_ref']['obj'])
        bounding_pyramid.fill(None, self.geom.bounds)

        meta = self.layer.get('meta', False)
        if meta:
            if option.zoom or option.zoom == 0:
                def metatilecoords(n, z):
                    xbounds, ybounds = bounding_pyramid.bounds[z]
                    metatilecoord = TileCoord(z, xbounds.start, ybounds.start).metatilecoord(n)
                    x = metatilecoord.x
                    while x < xbounds.stop:
                        y = metatilecoord.y
                        while y < ybounds.stop:
                            yield TileCoord(z, x, y, n)
                            y += n
                        x += n
                self.set_tilecoords(metatilecoords(self.layer['meta_size'], option.zoom))
            else:
                self.set_tilecoords(bounding_pyramid.metatilecoords(self.layer['meta_size']))
        elif options.zoom or option.zoom == 0:
            self.set_tilecoords(bounding_pyramid.ziter(options.zoom))
        else:
            self.set_tilecoords(bounding_pyramid)

    def set_tilecoords(self, tilecoords):
        self.tilestream = (
            Tile(tilecoord) for tilecoord in tilecoords)

    def set_store(self, store):
        self.tilestream = store.list()

#    def _wait(self, tile):
#        print 88888
#        print ApplyResult.wait(tile)
#        print tile.get()
#        return tile.get()

    def get(self, store, multiprocess=False):
#        if multiprocess and self.pool:
#            results = (self.pool.apply_async(Get(store), [t]) for t in self.tilestream)
#            async_results = (self.pool.apply_async(store.get_one, (t,)) for t in self.tilestream)
#            self.tilestream = imap(ApplyResult.wait, results)
#            self.tilestream = imap(ApplyResult.get, results)
#            self.tilestream = imap(self._wait, results)
#            self.tilestream = self.pool.imap(store.get_one, ifilter(None, self.tilestream))
#            self.tilestream = self.pool.imap(store.get_one, self.tilestream)
#        else:
            self.tilestream = store.get(self.tilestream)

    def put(self, store, multiprocess=False):
#        if multiprocess and self.pool:
#            results = (self.pool.apply_async(store.put_one, (t,)) for t in self.tilestream)
#            self.tilestream = imap(ApplyResult.wait, results)
#            self.tilestream = self.pool.imap(store.put_one, ifilter(None, self.tilestream))
#        else:
            self.tilestream = store.put(self.tilestream)

    def delete(self, store, multiprocess=False):
#        if multiprocess and self.pool:
#            results = (self.pool.apply_async(store.delete_one, (t,)) for t in self.tilestream)
#            self.tilestream = imap(ApplyResult.wait, results)
#            self.tilestream = self.pool.imap(store.delete_one, ifilter(None, self.tilestream))
#        else:
            self.tilestream = store.delete(self.tilestream)

    def imap(self, filter, multiprocess=False):
#        if multiprocess and self.pool:
#            async_results = (self.pool.apply_async(filter, (t,)) for t in self.tilestream)
#            self.tilestream = imap(ApplyResult.wait, async_results)
#            self.tilestream = self.pool.imap(filter, self.tilestream)
#        else:
            self.tilestream = imap(filter, self.tilestream)

    def ifilter(self, filter):
        self.tilestream = ifilter(filter, self.tilestream)


class HashDropper(object):
    """
    Create a filter to remove the tiles data where they have
    the specified size and hash.

    Used to drop the empty tiles.

    The ``store`` is used to delete the empty tiles.
    """

    def __init__(self, size, sha1code, store=None):
        self.size = size
        self.sha1code = sha1code
        self.store = store

    def __call__(self, tile):
        if len(tile.data) != self.size or \
                sha1(tile.data).hexdigest() != self.sha1code:
            return tile
        else:
            if self.store:
                self.store.delete_one(tile)
            return None


class HashLogger(object):  # pragma: no cover
    """
    Log the tile size and hash.
    """

    def __init__(self, block, logger):
        self.block = block
        self.logger = logger

    def __call__(self, tile):
        ref = None
        image = Image.open(StringIO(tile.data))
        for px in image.getdata():
            if ref is None:
                ref = px
            elif px != ref:
                self.logger.info("Warning: image is not uniform.")
                break

        self.logger.info("""Tile: %s
    %s:
        size: %i
        hash: %s""" % (str(tile.tilecoord), self.block, len(tile.data), sha1(tile.data).hexdigest()))
        return tile


class IntersectGeometryFilter(object):

    def __init__(self, grid, geom=None):
        self.grid = grid
        self.geom = geom or self.bbox_polygon(self.grid.max_extent)

    def __call__(self, tile):
        intersects = self.bbox_polygon(
                self.grid['obj'].extent(tile.tilecoord)). \
                intersects(self.geom)
        return tile if intersects else None

    def bbox_polygon(self, bbox):
        return Polygon((
                (bbox[0], bbox[1]),
                (bbox[0], bbox[3]),
                (bbox[2], bbox[3]),
                (bbox[2], bbox[1])))

    def bounds_polygon(self, bounds):
        return self.bbox_polygon((
                bounds[0].start, bounds[1].start,
                bounds[0].stop, bounds[1].stop))


class DropEmpty(object):
    """
    Create a filter for dropping all tiles with errors.
    """

    def __call__(self, tile):
        if not tile or not tile.data:
            return None
        else:
            return tile
