package com.datin.esb.legacy;

import jakarta.servlet.annotation.WebServlet;
import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;

/** استعلام وضعیت تراکنش (سرولت قدیمی) */
@WebServlet(urlPatterns = "/legacy/status")
public class StatusServlet extends HttpServlet {
    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        String transactionId = req.getParameter("transactionId");
        String apiKey = req.getHeader("ApiKey");
        resp.setContentType("application/json");
        resp.getWriter().write("{\"IsSuccess\":true}");
    }
}
