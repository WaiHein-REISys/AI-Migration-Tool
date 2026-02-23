// SOURCE EXAMPLE — from HAB-GPRSSubmission
// Used as a reference to test the transformer.
// See: Y:\Solution\HRSA\HAB-GPRSSubmission\src\GPRSSubmission.Web\wwwroot\gprs_app\Core\Services\Context.service.ts

import { Observable } from 'rxjs/Rx';
import { Http } from '@angular/http';
import { Injectable, Inject } from '@angular/core';

import { solutionConfig } from '../Config';
import { NavigationService } from './navigation.service';
import { REService } from 'pfm-re';
import { QueryStringUtil } from '../Utilities/querystring.util';
import { ContextArgsData } from './../models/context.args.data';
import { BaseService } from './base.service';
import { ContextModel } from './../models/context.model';
import { statusIcons, statusLookups, APP_CONSTANTS, getDate } from '../Utilities/appMappings';
import { FormInstanceCollectionArgsModel } from 'pfm-dcf'
import { FundingModel } from './Funding.service';
import { PFMHeaderModel, ResourceLinksModel } from 'pfm-ng/components';

@Injectable()
export class ContextService extends BaseService {
    public pageTitle: string = '';
    public contextModel: any;
    public readOnlyFriendlyNames: Array<string> = new Array<string>();
    public pdfFriendlyNames: Array<string> = new Array<string>();
    public readOnlySections: Array<any> = new Array<any>();
    navigationService: NavigationService;
    reService: REService;
    public fundingSources: FundingModel[] = [];

    constructor(
        @Inject(Http) public http: Http
    ) {
        super(http, "context");
        this.navigationService = new NavigationService();
    }

    public UpdateFundingSources(updated: FundingModel[]): void {
        this.fundingSources = updated;
        this.navigationService.fundingSources = updated;
    }

    getHeader(inputArgs: ContextArgsData, userId: string): Observable<PFMHeaderModel> {
        let args = inputArgs && inputArgs.friendlyName && inputArgs || this.getContextArgs(),
            url = "getHeader";
        let obj = {
            solutionName: args.solutionName,
            reviewId: args.reviewId,
            reportId: args.reportId,
            friendlyName: args.friendlyName,
            lastFriendlyName: args.lastFriendlyName,
            versionId: args.versionId,
            roleId: 0
        };
        return this.post(obj, url)
            .catch((error: any) => {
                return Observable.throw(error.status);
            });
    }

    getResources(inputArgs: ContextArgsData, userId: string): Observable<ResourceLinksModel> {
        let args = inputArgs && inputArgs.friendlyName && inputArgs || this.getContextArgs(),
            url = "getResources";
        let obj = {
            solutionName: args.solutionName,
            reviewId: args.reviewId,
            reportId: args.reportId,
            friendlyName: args.friendlyName,
            lastFriendlyName: args.lastFriendlyName,
            versionId: args.versionId,
            roleId: 0
        };
        return this.post(obj, url)
            .catch((error: any) => {
                return Observable.throw(error.status);
            });
    }
}
