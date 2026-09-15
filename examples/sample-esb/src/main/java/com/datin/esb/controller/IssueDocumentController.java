package com.datin.esb.controller;

import com.datin.esb.dto.IssueDocumentRequest;
import com.datin.esb.dto.IssueDocumentResponse;
import com.datin.esb.dto.ReverseRequest;
import com.datin.esb.dto.BaseResponse;
import com.datin.esb.service.DocumentService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;

/**
 * سرویس‌های مربوط به سند حسابداری.
 */
@RestController
@RequestMapping("/API")
public class IssueDocumentController {

    private final DocumentService documentService;

    public IssueDocumentController(DocumentService documentService) {
        this.documentService = documentService;
    }

    /**
     * ثبت سند حسابداری چند بندی (انتقال وجه داخلی).
     * با استفاده از این سرویس انتقال وجه در چند بند انجام و سند حسابداری مربوطه ثبت می‌گردد.
     *
     * @param accessToken توکن دسترسی با ساختار sso
     * @param businessToken توکن دسترسی پاد / برای تراکنش با مبدا پاد
     * @param request بدنه درخواست
     * @return نتیجه ثبت سند
     */
    @PostMapping(value = "/IssueDocument", consumes = "application/json", produces = "application/json")
    public IssueDocumentResponse issueDocument(@RequestHeader(value = "AccessToken", required = false) String accessToken,
                                               @RequestHeader(value = "BusinessToken", required = false) String businessToken,
                                               @Valid @RequestBody IssueDocumentRequest request) {
        return documentService.issue(request, accessToken, businessToken);
    }

    /**
     * استعلام وضعیت سند ثبت شده.
     */
    @GetMapping("/IssueDocument/{transactionId}")
    public IssueDocumentResponse getDocument(@PathVariable("transactionId") String transactionId,
                                             @RequestParam(value = "showDocumentNumber", required = false, defaultValue = "false") boolean showDocumentNumber) {
        return documentService.find(transactionId, showDocumentNumber);
    }
}
