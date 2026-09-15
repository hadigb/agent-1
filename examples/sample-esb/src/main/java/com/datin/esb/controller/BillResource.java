package com.datin.esb.controller;

import com.datin.esb.dto.ApiResponse;
import com.datin.esb.dto.BillStructureRequest;
import com.datin.esb.dto.BillStructureResult;
import com.datin.esb.dto.GenerateBillSerialRequest;
import com.datin.esb.dto.SerialNumberResult;
import com.datin.esb.error.BusinessException;
import com.datin.esb.error.ErrorCode;
import jakarta.validation.Valid;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.HeaderParam;
import jakarta.ws.rs.core.MediaType;

/**
 * سرویس‌های قبض سپرده (پیاده‌سازی با JAX-RS).
 */
@Path("/Api")
@Consumes(MediaType.APPLICATION_JSON)
@Produces(MediaType.APPLICATION_JSON)
public class BillResource {

    /**
     * نمایش نوع مشخصه عملیات مالی. این سرویس با ورود شماره سپرده دارای شناسه قبض، ساختار قبض سپرده را نمایش می‌دهد.
     */
    @POST
    @Path("/GetBillStructureForDeposite")
    public ApiResponse<BillStructureResult> getBillStructure(@HeaderParam("ApiKey") String apiKey,
                                                             @Valid BillStructureRequest request) {
        if (request.getDepositNumber() == null || request.getDepositNumber().isBlank()) {
            throw new BusinessException(ErrorCode.REQUIRED_PARAM_MISSING, "DepositNumber");
        }
        if (!request.getDepositNumber().matches("[0-9.]+")) {
            throw new BusinessException(ErrorCode.INVALID_DEPOSIT);
        }
        BillStructureResult result = new BillStructureResult();
        return ApiResponse.ok(result);
    }

    /**
     * دریافت شماره سریال قبض سپرده. این سرویس با دریافت اطلاعات سپرده، شناسه قبض سپرده را در خروجی نمایش می‌دهد.
     */
    @POST
    @Path("/GenerateBillSerialNumber")
    public ApiResponse<SerialNumberResult> generateBillSerialNumber(@Valid GenerateBillSerialRequest request) {
        SerialNumberResult result = new SerialNumberResult();
        return ApiResponse.ok(result);
    }
}
