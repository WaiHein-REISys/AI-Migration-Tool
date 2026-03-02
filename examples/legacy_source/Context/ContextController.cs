// SOURCE EXAMPLE — from HAB-GPRSSubmission
// Used as a reference to test the transformer.
// See: Y:\Solution\HRSA\HAB-GPRSSubmission\src\GPRSSubmission.Web\Areas\API\Controllers\ContextController.cs

using GPRS.Core.Contracts.Constants;
using GPRS.Core.Contracts.Interfaces.Services;
using GPRS.Core.Contracts.Models;
using GPRS.Core.Services.Services;
using GPRS.Core.Web;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Platform.Foundation8.Services.Url.Contracts;
using System;

namespace GPRSSubmission.Web.API.Controllers
{
    [Area("API")]
    [Route("api/context")]
    public class ContextController : SolutionBaseController
    {
        IContextService contextService;
        IUrlService urlService = null;

        public ContextController(IHttpContextAccessor accessor, IUrlService urlService, IContextService contextService) : base(accessor)
        {
            this.contextService = contextService;
            this.urlService = urlService;
        }

        [Route("getContext")]
        [HttpPost]
        [AllowAnonymous]
        public Object GetContext([FromBody]ContextArgsData args)
        {
            if (!ModelState.IsValid)
            {
                return null;
            }

            ContextModel contextModel = new ContextModel();

            try
            {
                var userId = base.UserId != null ? base.UserId.Value : new Guid();
                args.userId = userId;
                args.roleId = ExternalUserRoles.GRANTEE;
                contextModel = contextService.GetContext(args);
                contextModel.SourceData.ReportModel.GranteeTasksPageURL = urlService.GetUrl(new Guid("257132B4-A42C-447A-BB59-B6C35C9556E4"));
                var firstName = !String.IsNullOrEmpty(base.UserFirstName) ? base.UserFirstName : "User";
                var lastName = !String.IsNullOrEmpty(base.UserLastName) ? base.UserLastName : "User";

                contextModel.SourceData.UserData = new UserModel()
                {
                    UserId = userId,
                    FirstName = firstName,
                    LastName = lastName,
                    RoleId = ExternalUserRoles.GRANTEE,
                    RoleAbbr = "EU",
                    RoleName = "External User",
                    UserTypeCode = 1
                };
            }
            catch (Exception ex)
            {
                contextModel.Messages.Add(new MessageData() { Code = 1000, Message = "failed to get context." + "." + ex.Message });
            }
            return Ok(contextModel);
        }

        [HttpPost]
        [Route("getHeader")]
        public HeaderModel GetHeader([FromBody]ContextArgsData args)
        {
            if (!ModelState.IsValid)
            {
                return null;
            }

            try
            {
                var userId = base.UserId != null ? base.UserId.Value : new Guid();
                args.userId = userId;
                args.roleId = ExternalUserRoles.GRANTEE;
                HeaderModel headerModel = contextService.GetHeader(args);
                return headerModel;
            }
            catch (Exception ex)
            {
                return null;
            }
        }
    }
}
