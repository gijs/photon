import json
import os

import simplejson
import pysolr
import elasticsearch
from flask import Flask, render_template, request, abort, Response


app = Flask(__name__)
DEBUG = os.environ.get('DEBUG', True)
PORT = os.environ.get('PHOTON_PORT', 5001)
HOST = os.environ.get('PHOTON_HOST', '0.0.0.0')
SUPPORTED_LANGUAGES = ['de', 'en', 'fr', 'it']

solr = pysolr.Solr(os.environ.get("SOLR_ENDPOINT", 'http://localhost:8983/solr/testing'), timeout=10)

es = elasticsearch.Elasticsearch()


@app.route('/')
def index():
    return render_template('index.html')


def query_index(query, lang, lon, lat, match_all=True, limit=15):

    req_body = {
        "multi_match": {
            "query": query,
            "type": "best_fields",
            "analyzer": "search_stringanalyser",
            'minimum_should_match': '100%' if match_all else -1,
            "fields": [
                "name.{0}.ngramed^3".format(lang),
                "name.{0}.raw^10".format(lang),
                "collector.{0}.raw".format(lang),
                "collector.{0}".format(lang)
            ],
            "fuzziness": 1,
            "prefix_length": 3
        }
    }

    if lon is not None and lat is not None:
        req_body = {
            "function_score": {
                "score_mode": "multiply",  # How functions score are mixed together
                "boost_mode": "multiply",  # how total will be mixed to search score
                "query": req_body,
                "functions": [
                    {
                        "script_score": {
                            "script": "dist = doc['coordinate'].distanceInKm(lat, lon); 1 / (0.5 - 0.5 * exp(-5*dist/maxDist))",
                            "params": {
                                "lon": lon,
                                "lat": lat,
                                "maxDist": 100
                            }
                        }
                    },
                    {
                        "script_score": {
                            "script": "1 + doc['importance'].value * 40"
                        }
                    }
                ],
            }
        }

    # if housenumber is not null AND name.default is null, housenumber must
    # match the request. This will filter out the house from the requests
    # if the housenumber is not explicitelly in the request
    req_body = {
        "filtered": {
            "query": req_body,
            "filter": {
                "or": {
                    "filters": [
                        {
                            "missing": {
                                "field": "housenumber"
                            }
                        },
                        {
                            "query": {
                                "match": {
                                    "housenumber": {
                                        "query": query,
                                        "analyzer": "standard"
                                    }
                                }
                            }
                        },
                        {
                            "exists": {
                                "field": "name.{0}.raw".format(lang)
                            }
                        }
                    ]
                }
            }
        }
    }

    body = {"query": req_body, "size": limit}
    if DEBUG:
        print(json.dumps(body))
    return es.search(index="photon", body=body)


@app.route('/api/')
def api():
    lang = request.args.get('lang')
    if lang is None or lang not in SUPPORTED_LANGUAGES:
        lang = 'en'

    try:
        lon = float(request.args.get('lon'))
        lat = float(request.args.get('lat'))
    except (TypeError, ValueError):
        lon = lat = None

    try:
        limit = min(int(request.args.get('limit')), 50)
    except (TypeError, ValueError):
        limit = 15

    query = request.args.get('q')
    if not query:
        abort(400, "missing search term 'q': /?q=berlin")

    results = query_index(query, lang, lon, lat, limit=limit)

    if results['hits']['total'] < 1:
        # no result could be found, query index again and don't expect to match all search terms
        results = query_index(query, lang, lon, lat, match_all=False, limit=limit)

    debug = 'debug' in request.args
    data = to_geo_json(results['hits']['hits'], lang=lang, debug=debug)
    data = json.dumps(data, indent=4 if debug else None)
    return Response(data, mimetype='application/json')


@app.route('/api/solr/')
def api_solr():
    params = {}

    lang = request.args.get('lang')
    if lang is None or lang not in SUPPORTED_LANGUAGES:
        lang = 'en'

    try:
        lon = float(request.args.get('lon'))
        lat = float(request.args.get('lat'))
        params['qt'] = '{lang}_loc'.format(lang=lang)
        params['pt'] = '{lat},{lon}'.format(lon=lon, lat=lat)
    except (TypeError, ValueError):
        params['qt'] = '{lang}'.format(lang=lang)

    try:
        params['rows'] = min(int(request.args.get('limit')), 50)
    except (TypeError, ValueError):
        params['rows'] = 15

    query = request.args.get('q')
    if not query:
        abort(400, "missing search term 'q': /?q=berlin")

    results = solr.search(query, **params)
    data = json.dumps(to_geo_json_solr(results.docs, lang=lang))
    return Response(data, mimetype='application/json')


def housenumber_first(lang):
    if lang in ['de', 'it']:
        return False

    return True


def to_geo_json(hits, lang='en', debug=False):
    features = []
    for hit in hits:
        source = hit['_source']

        properties = {}

        if 'osm_id' in source:
            properties['osm_id'] = int(source['osm_id'])

        for attr in ['osm_key', 'osm_value', 'street', 'postcode', 'housenumber']:
            if attr in source:
                properties[attr] = source[attr]

        # language specific mapping
        for attr in ['name', 'country', 'city', 'street']:
            obj = source.get(attr, {})
            value = obj.get(lang) or obj.get('default')
            if value:
                properties[attr] = value

        if not 'name' in properties and 'housenumber' in properties:
            housenumber = properties['housenumber'] or ''
            street = properties['street'] or ''

            if housenumber_first(lang):
                properties['name'] = housenumber + ' ' + street
            else:
                properties['name'] = street + ' ' + housenumber

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [source['coordinate']['lon'], source['coordinate']['lat']]
            },
            "properties": properties
        }

        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features
    }


def to_geo_json_solr(docs, lang='en'):
    features = []

    for doc in docs:
        properties = {}
        for attr in ['osm_id', 'osm_key', 'osm_value', 'street', 'postcode', 'housenumber']:
            if attr in doc:
                properties[attr] = doc[attr]

        # language specific mapping
        for attr in ['name', 'country', 'city']:
            lang_attr = attr + "_" + lang
            value = doc.get(lang_attr) or doc.get(attr)
            if value:
                properties[attr] = value

        if not 'name' in properties and 'housenumber' in properties:
            if housenumber_first(lang):
                properties['name'] = properties['housenumber'] + ' ' + properties['street']
            else:
                properties['name'] = properties['street'] + ' ' + properties['housenumber']

        coordinates = [float(el) for el in doc['coordinate'].split(',')]
        coordinates.reverse()

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": coordinates
            },
            "properties": properties
        }

        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features
    }


@app.route('/search/')
def search():
    params = {
        "hl": 'true',
        "rows": 10
    }
    results = solr.search(request.args.get('q', '*:*'), **params)
    return simplejson.dumps({
        "docs": results.docs,
        "highlight": results.highlighting
    })


if __name__ == "__main__":
    app.run(debug=DEBUG, port=PORT, host=HOST)
