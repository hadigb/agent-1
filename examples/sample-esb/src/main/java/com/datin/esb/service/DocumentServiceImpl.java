package com.datin.esb.service;

import com.datin.esb.dto.DocumentItem;
import com.datin.esb.dto.IssueDocumentRequest;
import com.datin.esb.dto.IssueDocumentResponse;
import com.datin.esb.enums.DocumentItemType;
import com.datin.esb.error.BusinessException;
import com.datin.esb.error.ErrorCode;
import org.springframework.stereotype.Service;

@Service
public class DocumentServiceImpl implements DocumentService {

    private final CoreClient coreClient;
    private final TransactionRepository repository;

    public DocumentServiceImpl(CoreClient coreClient, TransactionRepository repository) {
        this.coreClient = coreClient;
        this.repository = repository;
    }

    @Override
    public IssueDocumentResponse issue(IssueDocumentRequest request, String accessToken, String businessToken) {
        if (repository.exists(request.getTransactionId())) {
            throw new BusinessException(ErrorCode.DUPLICATE_TRANSACTION);
        }
        validate(request);
        if (request.getGuildCode() != null && request.getWallet() != null) {
            throw new BusinessException(ErrorCode.INVALID_INPUT, "GuildCode");
        }
        CoreResult core = coreClient.issue(request, accessToken);
        IssueDocumentResponse response = new IssueDocumentResponse();
        response.setIsSuccess(true);
        response.setRsCode(ErrorCode.SUCCESS.getCode());
        response.setMessage(ErrorCode.SUCCESS.getMessage());
        response.setTransactionId(request.getTransactionId());
        response.setTransactionCode(core.getTransactionCode());
        if (Boolean.TRUE.equals(request.getShowDocumentNumber())) {
            response.setDocumentNumber(core.getDocumentNumber());
        }
        return response;
    }

    private void validate(IssueDocumentRequest request) {
        long debit = 0, credit = 0;
        for (DocumentItem item : request.getDocumentItem()) {
            if (item.getIsoCode() != null && item.getItemSOC() == null) {
                throw new BusinessException(ErrorCode.REQUIRED_PARAM_MISSING, "ItemSOC");
            }
            if (item.getDocumentItemType() == DocumentItemType.BOX && item.getAccountNumber() != null) {
                throw new BusinessException(ErrorCode.INVALID_INPUT, "AccountNumber");
            }
            if (Boolean.TRUE.equals(item.getIsDebtor())) debit += item.getAmount(); else credit += item.getAmount();
        }
        if (debit != credit) {
            throw new BusinessException(ErrorCode.UNBALANCED_DOCUMENT);
        }
    }

    @Override
    public IssueDocumentResponse find(String transactionId, boolean showDocumentNumber) {
        IssueDocumentResponse r = repository.find(transactionId);
        if (r == null) {
            throw new BusinessException(ErrorCode.INVALID_TRANSACTION_ID);
        }
        return r;
    }
}
