package com.datin.esb.service;

import com.datin.esb.dto.IssueDocumentRequest;
import com.datin.esb.error.BusinessException;
import com.datin.esb.error.ErrorCode;

public class CoreClient {
    public CoreResult issue(IssueDocumentRequest request, String accessToken) {
        try {
            return callCore(request);
        } catch (java.io.IOException e) {
            throw new BusinessException(ErrorCode.CORE_ERROR);
        }
    }

    private CoreResult callCore(IssueDocumentRequest request) throws java.io.IOException {
        return new CoreResult();
    }
}
