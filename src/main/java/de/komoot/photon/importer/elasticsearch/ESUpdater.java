package de.komoot.photon.importer.elasticsearch;

import de.komoot.photon.importer.Utils;
import de.komoot.photon.importer.model.PhotonDoc;
import lombok.extern.slf4j.Slf4j;
import org.elasticsearch.action.bulk.BulkRequestBuilder;
import org.elasticsearch.action.bulk.BulkResponse;
import org.elasticsearch.client.Client;

import java.io.IOException;

/**
 * Updater for elasticsearch
 *
 * @author felix
 */
@Slf4j
public class ESUpdater implements de.komoot.photon.importer.Updater {
	private Client esClient;
	private BulkRequestBuilder bulkRequest;

	public ESUpdater(Client esClient) {
		this.esClient = esClient;
		this.bulkRequest = esClient.prepareBulk();
	}

	public void finish() {
		this.updateDocuments();
	}

	@Override
	public void updateOrCreate(PhotonDoc updatedDoc) {
		final boolean exists = this.esClient.get(this.esClient.prepareGet("photon", "place", String.valueOf(updatedDoc.getPlaceId())).request()).actionGet().isExists();
		if(exists) {
			this.update(updatedDoc);
		} else {
			this.create(updatedDoc);
		}
	}

	public void create(PhotonDoc doc) {
		try {
			this.bulkRequest.add(this.esClient.prepareIndex("photon", "place").setSource(Utils.convert(doc)).setId(String.valueOf(doc.getPlaceId())));
		} catch(IOException e) {
			log.error(String.format("creation of new doc [%s] failed", doc), e);
		}
	}

	public void update(PhotonDoc doc) {
		try {
			this.bulkRequest.add(this.esClient.prepareUpdate("photon", "place", String.valueOf(doc.getPlaceId())).setDoc(Utils.convert(doc)));
		} catch(IOException e) {
			log.error(String.format("update of new doc [%s] failed", doc), e);
		}
	}

	public void delete(Long id) {
		this.bulkRequest.add(this.esClient.prepareDelete("photon", "place", String.valueOf(id)));
	}

	private void updateDocuments() {
		BulkResponse bulkResponse = bulkRequest.execute().actionGet();
		if(bulkResponse.hasFailures()) {
			log.error("error while bulk update: " + bulkResponse.buildFailureMessage());
		}
		this.bulkRequest = this.esClient.prepareBulk();
	}
}
