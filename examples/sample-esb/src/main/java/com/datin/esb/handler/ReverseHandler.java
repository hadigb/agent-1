package com.datin.esb.handler;

import com.datin.esb.dto.BaseResponse;
import com.datin.esb.dto.ReverseRequest;
import com.datin.esb.error.BusinessException;
import com.datin.esb.error.ErrorCode;
import com.datin.esb.service.TransactionRepository;

/**
 * سرویس برگشت تراکنش. از این سرویس جهت برگشت تراکنش‌های انتقال، واریز و برداشت و ... استفاده می‌گردد.
 */
@ServiceHandler(path = "/Api/Reverse", method = "POST")
public class ReverseHandler extends BaseHandler<ReverseRequest, BaseResponse> {

    private final TransactionRepository repository;

    public ReverseHandler(TransactionRepository repository) { this.repository = repository; }

    @Override
    protected BaseResponse execute(ReverseRequest request) {
        if (request.getTransactionId() == null) {
            throw new BusinessException(ErrorCode.INVALID_INPUT, "TransactionId");
        }
        if (!repository.exists(request.getTransactionId())) {
            throw new BusinessException(ErrorCode.INVALID_TRANSACTION_NUMBER);
        }
        BaseResponse response = new BaseResponse();
        response.setIsSuccess(true);
        response.setRsCode(ErrorCode.SUCCESS.getCode());
        response.setMessage(ErrorCode.SUCCESS.getMessage());
        return response;
    }
}
