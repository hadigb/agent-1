package com.datin.esb.service;

import com.datin.esb.dto.IssueDocumentRequest;
import com.datin.esb.dto.IssueDocumentResponse;

public interface DocumentService {
    IssueDocumentResponse issue(IssueDocumentRequest request, String accessToken, String businessToken);
    IssueDocumentResponse find(String transactionId, boolean showDocumentNumber);
}
