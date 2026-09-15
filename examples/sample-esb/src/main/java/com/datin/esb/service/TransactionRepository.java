package com.datin.esb.service;

import com.datin.esb.dto.IssueDocumentResponse;

public interface TransactionRepository {
    boolean exists(String transactionId);
    IssueDocumentResponse find(String transactionId);
}
