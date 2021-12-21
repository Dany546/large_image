##############################################################################
#  Copyright Kitware Inc.
#
#  Licensed under the Apache License, Version 2.0 ( the "License" );
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
##############################################################################

import datetime
import io
import math
import pickle
import time

import pymongo
from girder_large_image.models.image_item import ImageItem

from girder import logger
from girder.constants import AccessType, SortDir
from girder.models.file import File
from girder.models.item import Item
from girder.models.model_base import Model
from girder.models.upload import Upload

# Some annotation elements can be very large.  If they pass a size threshold,
# store part of them in an associated file.  This is slower, so don't do it for
# small ones.
MAX_ELEMENT_DOCUMENT = 10000


class Annotationelement(Model):
    bboxKeys = {
        'left': ('bbox.highx', '$gte'),
        'right': ('bbox.lowx', '$lt'),
        'top': ('bbox.highy', '$gte'),
        'bottom': ('bbox.lowy', '$lt'),
        'low': ('bbox.highz', '$gte'),
        'high': ('bbox.lowz', '$lt'),
        'minimumSize': ('bbox.size', '$gte'),
        'size': ('bbox.size', None),
        'details': ('bbox.details', None),
    }

    def initialize(self):
        self.name = 'annotationelement'
        self.ensureIndices([
            'annotationId',
            '_version',
            ([
                ('annotationId', SortDir.ASCENDING),
                ('bbox.lowx', SortDir.DESCENDING),
                ('bbox.highx', SortDir.ASCENDING),
                ('bbox.size', SortDir.DESCENDING),
            ], {}),
            ([
                ('annotationId', SortDir.ASCENDING),
                ('bbox.size', SortDir.DESCENDING),
            ], {}),
            ([
                ('annotationId', SortDir.ASCENDING),
                ('_version', SortDir.DESCENDING),
                ('element.group', SortDir.ASCENDING),
            ], {}),
            ([
                ('created', SortDir.ASCENDING),
                ('_version', SortDir.ASCENDING),
            ], {}),
        ])

        self.exposeFields(AccessType.READ, (
            '_id', '_version', 'annotationId', 'created', 'element'))
        self.versionId = None

    def getNextVersionValue(self):
        """
        Maintain a version number.  This is a single sequence that can be used
        to ensure we have the correct set of elements for an annotation.

        :returns: an integer version number that is strictly increasing.
        """
        version = None
        if self.versionId is not None:
            version = self.collection.find_one_and_update(
                {'_id': self.versionId},
                {'$inc': {'_version': 1}})
        if version is None:
            versionObject = self.collection.find_one(
                {'annotationId': 'version_sequence'})
            if versionObject is None:
                startingId = self.collection.find_one({}, sort=[('_version', SortDir.DESCENDING)])
                startingId = startingId['_version'] + 1 if startingId else 0
                self.versionId = self.collection.insert_one(
                    {'annotationId': 'version_sequence', '_version': startingId}
                ).inserted_id
            else:
                self.versionId = versionObject['_id']
            version = self.collection.find_one_and_update(
                {'_id': self.versionId},
                {'$inc': {'_version': 1}})
        return version['_version']

    def getElements(self, annotation, region=None):
        """
        Given an annotation, fetch the elements from the database and add them
        to it.

        When a region is used to request specific element, the following
        keys can be specified:

            :left, right, top, bottom, low, high: the spatial area where
                elements are located, all in pixels.  If an element's bounding
                box is at least partially within the requested area, that
                element is included.
            :minimumSize: the minimum size of an element to return.
            :sort, sortdir: standard sort options.  The sort key can include
                size and details.
            :limit: limit the total number of elements by this value.  Defaults
                to no limit.
            :offset: the offset within the query to start returning values.  If
                maxDetails is used, to get subsequent sets of elements, the
                offset needs to be increased by the actual number of elements
                returned from a previous query, which will vary based on the
                details of the elements.
            :maxDetails: if specified, limit the total number of elements by
                the sum of their details values.  This is applied in addition
                to limit.  The sum of the details values of the elements may
                exceed maxDetails slightly (the sum of all but the last element
                will be less than maxDetails, but the last element may exceed
                the value).
            :centroids: if specified and true, only return the id, center of
                the bounding box, and bounding box size for each element.

        :param annotation: the annotation to get elements for.  Modified.
        :param region: if present, a dictionary restricting which annotations
            are returned.
        """
        annotation['_elementQuery'] = {}
        annotation['annotation']['elements'] = [
            element for element in self.yieldElements(
                annotation, region, annotation['_elementQuery'])]

    def yieldElements(self, annotation, region=None, info=None):  # noqa
        """
        Given an annotation, fetch the elements from the database.

        When a region is used to request specific element, the following
        keys can be specified:

            :left, right, top, bottom, low, high: the spatial area where
                elements are located, all in pixels.  If an element's bounding
                box is at least partially within the requested area, that
                element is included.
            :minimumSize: the minimum size of an element to return.
            :sort, sortdir: standard sort options.  The sort key can include
                size and details.
            :limit: limit the total number of elements by this value.  Defaults
                to no limit.
            :offset: the offset within the query to start returning values.  If
                maxDetails is used, to get subsequent sets of elements, the
                offset needs to be increased by the actual number of elements
                returned from a previous query, which will vary based on the
                details of the elements.
            :maxDetails: if specified, limit the total number of elements by
                the sum of their details values.  This is applied in addition
                to limit.  The sum of the details values of the elements may
                exceed maxDetails slightly (the sum of all but the last element
                will be less than maxDetails, but the last element may exceed
                the value).
            :centroids: if specified and true, only return the id, center of
                the bounding box, and bounding box size for each element.

        :param annotation: the annotation to get elements for.  Modified.
        :param region: if present, a dictionary restricting which annotations
            are returned.
        :param info: an optional dictionary that will be modified with
            additional query information, including count (total number of
            available elements), returned (number of elements in response),
            maxDetails (as specified by the region dictionary), details (sum of
            details returned), limit (as specified by region), centroids (a
            boolean based on the region specification).
        :returns: a list of elements.  If centroids were requested, each entry
            is a list with str(id), x, y, size.  Otherwise, each entry is the
            element record.
        """
        info = info if info is not None else {}
        region = region or {}
        query = {
            'annotationId': annotation.get('_annotationId', annotation['_id']),
            '_version': annotation['_version']
        }
        for key in region:
            if key in self.bboxKeys and self.bboxKeys[key][1]:
                if self.bboxKeys[key][1] == '$gte' and float(region[key]) <= 0:
                    continue
                query[self.bboxKeys[key][0]] = {
                    self.bboxKeys[key][1]: float(region[key])}
        if region.get('sort') in self.bboxKeys:
            sortkey = self.bboxKeys[region['sort']][0]
        else:
            sortkey = region.get('sort') or '_id'
        sortdir = int(region['sortdir']) if region.get('sortdir') else SortDir.ASCENDING
        limit = int(region['limit']) if region.get('limit') else 0
        maxDetails = int(region.get('maxDetails') or 0)
        queryLimit = maxDetails if maxDetails and (not limit or maxDetails < limit) else limit
        offset = int(region['offset']) if region.get('offset') else 0
        logger.debug('element query %r for %r', query, region)
        fields = {'_id': True, 'element': True, 'bbox.details': True, 'datafile': True}
        centroids = str(region.get('centroids')).lower() == 'true'
        if centroids:
            # fields = {'_id': True, 'element': True, 'bbox': True}
            fields = {
                '_id': True,
                'element.id': True,
                'bbox': True}
            proplist = []
            propskeys = ['type', 'fillColor', 'lineColor', 'lineWidth', 'closed']
            # This should match the javascript
            defaultProps = {
                'fillColor': 'rgba(0,0,0,0)',
                'lineColor': 'rgb(0,0,0)',
                'lineWidth': 2,
            }
            for key in propskeys:
                fields['element.%s' % key] = True
            props = {}
            info['centroids'] = True
            info['props'] = proplist
            info['propskeys'] = propskeys
        elementCursor = self.find(
            query=query, sort=[(sortkey, sortdir)], limit=queryLimit,
            offset=offset, fields=fields)

        info.update({
            'count': elementCursor.count(),
            'offset': offset,
            'filter': query,
            'sort': [sortkey, sortdir],
        })
        details = count = 0
        if maxDetails:
            info['maxDetails'] = maxDetails
        if limit:
            info['limit'] = limit
        for entry in elementCursor:
            element = entry['element']
            element.setdefault('id', entry['_id'])
            if centroids:
                bbox = entry.get('bbox')
                if not bbox or 'lowx' not in bbox or 'size' not in bbox:
                    continue
                prop = tuple(
                    element.get(key, defaultProps.get(key)) for key in propskeys
                    if element.get(key, defaultProps.get(key)) is not None)
                if prop not in props:
                    props[prop] = len(props)
                    proplist.append(list(prop))
                yield [
                    str(element['id']),
                    (bbox['lowx'] + bbox['highx']) / 2,
                    (bbox['lowy'] + bbox['highy']) / 2,
                    bbox['size'] if entry.get('type') != 'point' else 0,
                    props[prop]
                ]
                details += 1
            else:
                if entry.get('datafile'):
                    datafile = entry['datafile']
                    data = io.BytesIO()
                    chunksize = 1024 ** 2
                    with File().open(File().load(datafile['fileId'], force=True)) as fptr:
                        while True:
                            chunk = fptr.read(chunksize)
                            if not len(chunk):
                                break
                            data.write(chunk)
                    data.seek(0)
                    element[datafile['key']] = pickle.load(data)
                yield element
                details += entry.get('bbox', {}).get('details', 1)
            count += 1
            if maxDetails and details >= maxDetails:
                break
        info['returned'] = count
        info['details'] = details

    def removeWithQuery(self, query):
        """
        Remove all documents matching a given query from the collection.
        For safety reasons, you may not pass an empty query.

        Note: this does NOT return a Mongo DeleteResult.

        :param query: The search query for documents to delete,
            see general MongoDB docs for "find()"
        :type query: dict
        """
        if not query:
            raise Exception('query must be specified')

        attachedQuery = query.copy()
        attachedQuery['datafile'] = {'$exists': True}
        for element in self.collection.find(attachedQuery):
            file = File().load(element['datafile']['fileId'], force=True)
            if file:
                File().remove(file)
        self.collection.bulk_write([pymongo.DeleteMany(query)], ordered=False)

    def removeElements(self, annotation):
        """
        Remove all elements related to the specified annotation.

        :param annotation: the annotation to remove elements from.
        """
        self.removeWithQuery({'annotationId': annotation['_id']})

    def removeOldElements(self, annotation, oldversion=None):
        """
        Remove all elements related to the specified annotation.

        :param annotation: the annotation to remove elements from.
        :param oldversion: if present, remove versions up to this number.  If
                           none, remove versions earlier than the version in
                           the annotation record.
        """
        query = {'annotationId': annotation['_id']}
        if oldversion is None or oldversion >= annotation['_version']:
            query['_version'] = {'$lt': annotation['_version']}
        else:
            query['_version'] = {'$lte': oldversion}
        self.removeWithQuery(query)

    def _overlayBounds(self, overlayElement):
        """
        Compute bounding box information in the X-Y plane for an
        image overlay element.

        This uses numpy to perform the specified transform on the given girder
        image item in order to obtain bounding box coordinates.

        :param overlayElement: An annotation element of type 'imageoverlay'.
        :returns: a tuple with 4 values: lowx, highx, lowy, highy. Runtime exceptions
         during loading the image metadata will result in the tuple (0, 0, 0, 0).
        """
        if overlayElement.get('type') != 'imageoverlay':
            raise ValueError(
                'Function _overlayBounds only accepts annotation elements of type "imageoverlay"'
            )

        import numpy as np
        lowx = highx = lowy = highy = 0

        try:
            overlayItemId = overlayElement.get('girderId')
            imageItem = ImageItem().load(overlayItemId, force=True)
            overlayImageMetadata = ImageItem().getMetadata(imageItem)
            corners = [
                [0, 0],
                [0, overlayImageMetadata['sizeY']],
                [overlayImageMetadata['sizeX'], overlayImageMetadata['sizeY']],
                [overlayImageMetadata['sizeX'], 0],
            ]
            transform = overlayElement.get('transform', {})
            transformMatrix = np.array(transform.get('matrix', [[1, 0], [0, 1]]))
            corners = [np.matmul(np.array(corner), transformMatrix) for corner in corners]
            offsetArray = np.array([transform.get('xoffset', 0), transform.get('yoffset', 0)])
            corners = [np.add(corner, offsetArray) for corner in corners]
            # use .item() to convert back to native python types
            lowx = min([corner[0] for corner in corners]).item()
            highx = max([corner[0] for corner in corners]).item()
            lowy = min([corner[1] for corner in corners]).item()
            highy = max([corner[1] for corner in corners]).item()
        except Exception:
            logger.exception('Error generating bounding box for image overlay annotation')
        return lowx, highx, lowy, highy

    def _boundingBox(self, element):
        """
        Compute bounding box information for an annotation element.

        This computes the enclosing bounding box of an element.  For points, an
        small non-zero-area region is used centered on the point.
        Additionally, a metric is stored for the complexity of the element.
        The size of the bounding box's x-y diagonal is also stored.

        :param element: the element to compute the bounding box for.
        :returns: the bounding box dictionary.  This contains 'lowx', 'lowy',
            'lowz', 'highx', 'highy', and 'highz, which are the minimum and
            maximum values in each dimension, 'details' with the complexity of
            the element, and 'size' with the x-y diagonal size of the bounding
            box.
        """
        bbox = {}
        if 'points' in element:
            key = 'points'
            bbox['lowx'] = min(p[0] for p in element[key])
            bbox['lowy'] = min(p[1] for p in element[key])
            bbox['lowz'] = min(p[2] for p in element[key])
            bbox['highx'] = max(p[0] for p in element[key])
            bbox['highy'] = max(p[1] for p in element[key])
            bbox['highz'] = max(p[2] for p in element[key])
            bbox['details'] = len(element[key])
        elif element.get('type') == 'griddata':
            x0, y0, z = element['origin']
            isElements = element.get('interpretation') == 'choropleth'
            x1 = x0 + element['dx'] * (element['gridWidth'] - (1 if not isElements else 0))
            y1 = y0 + element['dy'] * (math.ceil(len(element['values']) / element['gridWidth']) -
                                       (1 if not isElements else 0))
            bbox['lowx'] = min(x0, x1)
            bbox['lowy'] = min(y0, y1)
            bbox['lowz'] = bbox['highz'] = z
            bbox['highx'] = max(x0, x1)
            bbox['highy'] = max(y0, y1)
            bbox['details'] = len(element['values'])
        elif element.get('type') == 'imageoverlay':
            lowx, highx, lowy, highy = Annotationelement()._overlayBounds(element)
            bbox['lowz'] = bbox['highz'] = 0
            bbox['lowx'] = lowx
            bbox['highx'] = highx
            bbox['lowy'] = lowy
            bbox['highy'] = highy
            bbox['details'] = 1
        else:
            center = element['center']
            bbox['lowz'] = bbox['highz'] = center[2]
            if 'width' in element:
                w = element['width'] * 0.5
                h = element['height'] * 0.5
                if element.get('rotation'):
                    absin = abs(math.sin(element['rotation']))
                    abcos = abs(math.cos(element['rotation']))
                    w, h = max(abcos * w, absin * h), max(absin * w, abcos * h)
                bbox['lowx'] = center[0] - w
                bbox['lowy'] = center[1] - h
                bbox['highx'] = center[0] + w
                bbox['highy'] = center[1] + h
                bbox['details'] = 4
            elif 'radius' in element:
                rad = element['radius']
                bbox['lowx'] = center[0] - rad
                bbox['lowy'] = center[1] - rad
                bbox['highx'] = center[0] + rad
                bbox['highy'] = center[1] + rad
                bbox['details'] = 4
            else:
                # This is a fall back for points.  Although they have no
                # dimension, make the bounding box have some extent.
                bbox['lowx'] = center[0] - 0.5
                bbox['lowy'] = center[1] - 0.5
                bbox['highx'] = center[0] + 0.5
                bbox['highy'] = center[1] + 0.5
                bbox['details'] = 1
        bbox['size'] = (
            (bbox['highy'] - bbox['lowy'])**2 +
            (bbox['highx'] - bbox['lowx'])**2) ** 0.5
        # we may want to store perimeter or area as that could help when we
        # simplify to points
        return bbox

    def saveElementAsFile(self, annotation, entries):
        """
        If an element has a large points or values array, save that array to an
        attached file.

        :param annotation: the parent annotation.
        :param entries: the database entries document.  Modified.
        """
        item = Item().load(annotation['itemId'], force=True)
        element = entries[0]['element'].copy()
        entries[0]['element'] = element
        key = 'points' if 'points' in element else 'values'
        # Use the highest protocol support by all python versions we support
        data = pickle.dumps(element.pop(key), protocol=4)
        elementFile = Upload().uploadFromFile(
            io.BytesIO(data), size=len(data), name='_annotationElementData',
            parentType='item', parent=item, user=None,
            mimeType='application/json', attachParent=True)
        entries[0]['datafile'] = {
            'key': key,
            'fileId': elementFile['_id'],
        }

    def updateElements(self, annotation):
        """
        Given an annotation, extract the elements from it and update the
        database of them.

        :param annotation: the annotation to save elements for.  Modified.
        """
        startTime = lastTime = time.time()
        elements = annotation['annotation'].get('elements', [])
        if not len(elements):
            return
        now = datetime.datetime.utcnow()
        chunkSize = 100000
        for chunk in range(0, len(elements), chunkSize):
            chunkStartTime = time.time()
            entries = [{
                'annotationId': annotation['_id'],
                '_version': annotation['_version'],
                'created': now,
                'bbox': self._boundingBox(element),
                'element': element
            } for element in elements[chunk:chunk + chunkSize]]
            prepTime = time.time() - chunkStartTime
            if (len(entries) == 1 and len(entries[0]['element'].get(
                    'points', entries[0]['element'].get('values', []))) > MAX_ELEMENT_DOCUMENT):
                self.saveElementAsFile(annotation, entries)
            res = self.collection.insert_many(entries)
            for pos, entry in enumerate(entries):
                if 'id' not in entry['element']:
                    entry['element']['id'] = str(res.inserted_ids[pos])
            # If the whole insert is slow, log information about it.
            if time.time() - lastTime > 10:
                logger.info('insert %d elements in %4.2fs (prep time %4.2fs), done %d/%d' % (
                    len(entries), time.time() - chunkStartTime, prepTime,
                    chunk + len(entries), len(elements)))
                lastTime = time.time()
        if time.time() - startTime > 10:
            logger.info('inserted %d elements in %4.2fs' % (
                len(elements), time.time() - startTime))

    def getElementGroupSet(self, annotation):
        query = {
            'annotationId': annotation.get('_annotationId', annotation['_id']),
            '_version': annotation['_version']
        }
        groups = sorted([
            group for group in self.collection.distinct('element.group', filter=query)
            if isinstance(group, str)
        ])
        query['element.group'] = None
        if self.collection.find_one(query):
            groups.append(None)
        return groups
